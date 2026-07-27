"""Centralized, multilingual, provider-agnostic categorization engine.

The stored category is always a language-neutral **code**; per-language labels
live in ``config/categories.yaml`` (single i18n source of truth).

Resolver chain (order = priority), over **universal signals** present in any bank
export -- NOT provider taxonomies:
1. **MCC** (ISO 18245 standard) -> code, high precision;
2. **rules** (bilingual IT+EN regex) over the normalized description;
3. **model** (our classifier) if ``confidence >= threshold``;
4. **default** (``other``).

Native provider categories are NOT a resolver: at most an optional weak signal
for **bootstrapping the training set** (see ``seed_code()``, used only when
explicitly enabled), never a runtime authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from cashato.config import CONFIG_DIR, setting

from .base import normalize_desc

_CONFIG_PATH = CONFIG_DIR / "categories.yaml"
_MCC_PATH = CONFIG_DIR / "mcc.yaml"
_FALLBACK_THRESHOLD = 0.6  # when settings.yaml has no categorization.model_threshold


@dataclass
class Result:
    """A categorization outcome: code + confidence + provenance."""

    code: str
    confidence: float
    source: str  # mcc | rule | model | manual | default


def build_text(description: str) -> str:
    """Model feature text, shared between training and inference.

    Only basic normalization (lowercase, no accents): no provider-specific regex
    cleaning, no source token. Noise (dates, codes, masked card numbers) is left
    to the model -- best handled by semantic **embeddings**, robust to noise and
    able to generalize to unseen merchants/languages."""
    return normalize_desc(description)


class Categorizer:
    def __init__(
        self,
        config: dict,
        mcc_map: dict | None = None,
        model=None,
        model_threshold: float | None = None,
    ):
        self.default: str = config.get("default", "other")
        self.categories: dict[str, dict[str, str]] = config.get("categories", {})
        self.rules = [
            (re.compile(r["pattern"], re.IGNORECASE), r["category"])
            for r in config.get("rules", [])
        ]
        # mcc.yaml keys are exact 4-digit codes or "lo-hi" ranges. Ranges cover
        # the brand-specific ISO blocks (3000-3299 airlines, 3300-3499 car
        # rentals, 3500-3999 lodging): one code PER BRAND, so enumerating them
        # would be ~1000 entries mapping to the same category.
        self.mcc_map: dict[str, str] = {}
        self.mcc_ranges: list[tuple[int, int, str]] = []
        for k, v in (mcc_map or {}).items():
            key = str(k)
            if "-" in key:
                lo, hi = key.split("-", 1)
                self.mcc_ranges.append((int(lo), int(hi), v))
            else:
                self.mcc_map[key] = v
        # seeds: only for optional training bootstrap, NOT for runtime
        self._seeds_ci = {
            src: {str(k).strip().lower(): v for k, v in m.items()}
            for src, m in config.get("seeds", {}).items()
        }
        self.model = model
        # Read at CONSTRUCTION, not import, so a config reload is seen.
        self.model_threshold = (
            float(setting("categorization.model_threshold", _FALLBACK_THRESHOLD))
            if model_threshold is None
            else model_threshold
        )

    @classmethod
    def load(
        cls,
        config_path: str | Path = _CONFIG_PATH,
        mcc_path: str | Path = _MCC_PATH,
        model=None,
        model_threshold: float | None = None,
    ) -> Categorizer:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        mcc_map: dict = {}
        if Path(mcc_path).exists():
            with open(mcc_path, encoding="utf-8") as f:
                mcc_map = yaml.safe_load(f) or {}
        return cls(config, mcc_map, model, model_threshold)

    @property
    def languages(self) -> list[str]:
        langs: set[str] = set()
        for labels in self.categories.values():
            langs.update(labels.keys())
        return sorted(langs)

    def mcc_category(self, mcc: str) -> str | None:
        """Category for an MCC: exact entry first, then the range blocks."""
        key = str(mcc).strip()
        code = self.mcc_map.get(key)
        if code:
            return code
        if key.isdigit():
            n = int(key)
            for lo, hi, cat in self.mcc_ranges:
                if lo <= n <= hi:
                    return cat
        return None

    # --- runtime: provider-agnostic resolver chain ---
    def resolve(
        self,
        description: str,
        source: str | None = None,
        mcc: str | None = None,
    ) -> Result:
        # 1. MCC (ISO standard): exact, highest precision
        if mcc:
            mcc_code = self.mcc_category(mcc)
            if mcc_code:
                return Result(mcc_code, 1.0, "mcc")
        # 2. embedding model (does the bulk of the work) if confident
        if self.model is not None:
            pred = self._predict(description)
            if pred and pred[1] >= self.model_threshold:
                return Result(pred[0], pred[1], "model")
        # 3. keyword rules: thin, high-precision safety net
        norm = normalize_desc(description)
        for pattern, code in self.rules:
            if pattern.search(norm):
                return Result(code, 0.9, "rule")
        # 4. default
        return Result(self.default, 0.0, "default")

    def categorize(
        self,
        description: str,
        source: str | None = None,
        mcc: str | None = None,
    ) -> str:
        return self.resolve(description, source, mcc).code

    def resolve_many(self, items: list[tuple[str, str | None, str | None]]) -> list[Result]:
        """Batch version of :meth:`resolve` for large volumes (same order:
        MCC -> model -> rules -> default), with a **single encode** for the model."""
        results: list[Result | None] = [None] * len(items)
        pending_idx: list[int] = []
        pending_desc: list[str] = []
        for i, (descr, _source, mcc) in enumerate(items):
            if mcc and (code := self.mcc_category(mcc)):
                results[i] = Result(code, 1.0, "mcc")
            else:
                pending_idx.append(i)
                pending_desc.append(descr)
        batch = (
            self.model.predict_batch([build_text(d) for d in pending_desc])
            if self.model is not None
            else [None] * len(pending_desc)
        )
        for k, i in enumerate(pending_idx):
            pred = batch[k]
            if pred and pred[1] >= self.model_threshold:
                results[i] = Result(pred[0], pred[1], "model")
                continue
            norm = normalize_desc(pending_desc[k])
            code = next((c for pat, c in self.rules if pat.search(norm)), None)
            results[i] = Result(code, 0.9, "rule") if code else Result(self.default, 0.0, "default")
        return results  # type: ignore[return-value]

    def _predict(self, description: str) -> tuple[str, float] | None:
        try:
            text = build_text(description)
            # embedding model (EmbeddingKNN): predict_one -> (code, confidence)
            if hasattr(self.model, "predict_one"):
                return self.model.predict_one(text)
            # compat: sklearn classifier with predict_proba
            proba = self.model.predict_proba([text])[0]
            classes = self.model.classes_
            best = max(range(len(proba)), key=lambda i: proba[i])
            return str(classes[best]), float(proba[best])
        except Exception:
            return None

    # --- optional bootstrap: native category -> code (NOT runtime) ---
    def seed_code(self, native_category: str | None, source: str | None) -> str | None:
        if not native_category or not source:
            return None
        return self._seeds_ci.get(source, {}).get(native_category.strip().lower())

    def label(self, code: str | None, lang: str = "it") -> str:
        if not code:
            return ""
        labels = self.categories.get(code, {})
        return labels.get(lang) or labels.get("en") or code
