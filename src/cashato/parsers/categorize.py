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
from .merchant import extract_merchant

_CONFIG_PATH = CONFIG_DIR / "categories.yaml"
_MCC_PATH = CONFIG_DIR / "mcc.yaml"
# When settings.yaml has no categorization.model_threshold. Mirrors the
# shipped config value so an unmounted settings.yaml degrades to the SAME
# behavior, not a silently different one.
_FALLBACK_THRESHOLD = 0.75

# Everything that may ever stamp silver.transactions.category_source, in
# resolver-priority order. The DB enforces the same vocabulary with a CHECK
# constraint; /meta publishes this tuple so clients never restate it.
CATEGORY_SOURCES = ("mcc", "model", "rule", "manual", "default")


@dataclass
class Result:
    """A categorization outcome: code + confidence + provenance."""

    code: str
    confidence: float
    source: str  # mcc | rule | model | manual | default


def build_text(description: str, source: str | None = None) -> str:
    """Model feature text, shared between training and inference.

    When the source is known and the description carries a merchant, the
    merchant IS the feature text: in a POS line the counterparty is a handful
    of tokens drowning in boilerplate ("Pagamento POS EFFETTUATO IL ... PRESSO
    IKEA"), and embedding the whole line made every POS payment look like every
    other. Rows without a merchant (wire transfers, salaries) keep the full
    text — there the operation wording is exactly the signal.

    Otherwise only basic normalization (lowercase, no accents): remaining noise
    is left to the semantic **embeddings**, robust and able to generalize to
    unseen merchants/languages."""
    if source:
        merchant = extract_merchant(source, description).merchant
        if merchant:
            return normalize_desc(merchant)
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
        # Wealth-not-consumption codes: spend figures exclude these. The SQL
        # twin is silver.asset_categories (gold's views read the table); a
        # test keeps the two in step. Validated eagerly so a typo fails the
        # service at startup instead of quietly mis-counting spend.
        self.asset_categories: frozenset[str] = frozenset(config.get("asset_categories", []))
        unknown = self.asset_categories - self.categories.keys()
        if unknown:
            raise ValueError(f"asset_categories not in categories: {sorted(unknown)}")
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
        # 2/3. Model and keyword rules, ORDERED PER ROW. With a merchant the
        # model leads (embeddings generalize to unseen merchants; rules only
        # know a fixed list). Without one the feature text is operation
        # boilerplate where every wire transfer looks like every other — the
        # distinguishing word ("Affitto") drowns for the embedding but is
        # EXACTLY what a keyword rule reads, so rules lead there.
        rule_hit = self._rule(description)
        merchant_led = self._has_merchant(description, source)
        order = ("model", "rule") if merchant_led else ("rule", "model")
        for step in order:
            if step == "rule" and rule_hit:
                return Result(rule_hit, 0.9, "rule")
            if step == "model" and self.model is not None:
                pred = self._predict(description, source)
                if pred and pred[1] >= self.model_threshold:
                    return Result(pred[0], pred[1], "model")
        # 4. default
        return Result(self.default, 0.0, "default")

    def _rule(self, description: str) -> str | None:
        norm = normalize_desc(description)
        return next((code for pattern, code in self.rules if pattern.search(norm)), None)

    @staticmethod
    def _has_merchant(description: str, source: str | None) -> bool:
        return source is not None and extract_merchant(source, description).merchant is not None

    def categorize(
        self,
        description: str,
        source: str | None = None,
        mcc: str | None = None,
    ) -> str:
        return self.resolve(description, source, mcc).code

    def resolve_many(self, items: list[tuple[str, str | None, str | None]]) -> list[Result]:
        """Batch version of :meth:`resolve` (same per-row ordering), with a
        **single encode** for every row that reaches the model."""
        results: list[Result | None] = [None] * len(items)
        pending_idx: list[int] = []
        pending_desc: list[str] = []
        pending_src: list[str | None] = []
        for i, (descr, source, mcc) in enumerate(items):
            if mcc and (code := self.mcc_category(mcc)):
                results[i] = Result(code, 1.0, "mcc")
                continue
            # Merchant-less rows resolve by rule BEFORE the model (see resolve).
            if not self._has_merchant(descr, source) and (hit := self._rule(descr)):
                results[i] = Result(hit, 0.9, "rule")
                continue
            pending_idx.append(i)
            pending_desc.append(descr)
            pending_src.append(source)
        batch = (
            self.model.predict_batch(
                [build_text(d, s) for d, s in zip(pending_desc, pending_src, strict=True)]
            )
            if self.model is not None
            else [None] * len(pending_desc)
        )
        for k, i in enumerate(pending_idx):
            pred = batch[k]
            if pred and pred[1] >= self.model_threshold:
                results[i] = Result(pred[0], pred[1], "model")
                continue
            code = self._rule(pending_desc[k])
            results[i] = Result(code, 0.9, "rule") if code else Result(self.default, 0.0, "default")
        return results  # type: ignore[return-value]

    def _predict(self, description: str, source: str | None = None) -> tuple[str, float] | None:
        try:
            text = build_text(description, source)
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
