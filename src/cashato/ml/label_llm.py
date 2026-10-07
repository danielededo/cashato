"""Offline labeling with a local LLM (Ollama).

Generates **canonical** labels (in OUR taxonomy) for the descriptions the
fast-path leaves unresolved, so the training set does not depend on provider
categories. Fully local (privacy).

Two populations are sent to the LLM, the slow model teaching the fast one:

- the long tail: descriptions the resolver chain left on the default code;
- the **contested** rows: descriptions the kNN DID categorize (above the
  confidence threshold) but on a split vote — ``Prediction.margin`` below
  ``--margin``. A wrong answer at 0.76 confidence never lands in the queue the
  long tail does; without this, only a manual correction ever revisits it.
  Needs ``models/latest.joblib`` (written by ``train``); absent, only the long
  tail is labeled, as before.

Requires a running Ollama:
    curl -fsSL https://ollama.com/install.sh | sh   # once
    ollama pull qwen2.5:7b   # labeling is offline+batch, so prefer the largest
                             # model the GPU fits — label quality caps the model

Usage:
    ./.venv/bin/python ml/label_llm.py --model qwen2.5:7b --limit 500
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from typing import Any

from sqlalchemy import text

from cashato.config import MODEL_DIR, setting
from cashato.db.db import get_engine
from cashato.parsers.categorize import Categorizer, build_text

MODEL_PATH = MODEL_DIR / "latest.joblib"
# Below this vote margin a model-resolved row is re-asked to the LLM. With
# k=5 and even similarities 4-1 scores ~0.6 and 3-2 ~0.2, so 0.5 reads as
# "fewer than four of five neighbors agreed".
DEFAULT_MARGIN = float(setting("categorization.escalate_margin", 0.5))

OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/") + "/api/chat"
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")

# Few-shot examples (Italian merchant text is real-world data, kept as-is).
_FEWSHOT = [
    ("pagamento pos presso mykonos taverna greca milano", "dining"),
    ("pagamento pos presso esselunga spa", "groceries"),
    ("pagamento su pos uniqlo milano", "shopping"),
    ("pagamento pos presso trenitalia", "transport"),
    ("bonifico a vostro favore disposto da mario rossi", "transfers"),
    ("apple pay top up by 3416", "transfers"),
    ("stipendio o pensione accredito", "salary"),
    ("prelievo bancomat atm", "cash"),
    ("netflix", "subscriptions"),
    ("forship xyz", "other"),
]


def _prompt(cat: Categorizer) -> str:
    codes = "\n".join(
        f"- {code}: {lbl.get('it')} / {lbl.get('en')}" for code, lbl in cat.categories.items()
    )
    examples = "\n".join(f'  "{d}" -> {{"category": "{c}"}}' for d, c in _FEWSHOT)
    return (
        "You are a bank transaction classifier. Assign each description ONE "
        "category, returning the EXACT code among these:\n"
        f"{codes}\n\n"
        "IMPORTANT RULES:\n"
        "- A 'pagamento POS' / card payment / card transaction is a PURCHASE at a "
        "merchant: categorize by the MERCHANT (e.g. restaurant->dining, "
        "supermarket->groceries, shop->shopping), NOT as transfers.\n"
        "- Use 'transfers' ONLY for bank transfers, giro, top-ups between accounts/people.\n"
        "- Ignore noise such as card numbers (xxxx), dates, times, POS/ABI codes: "
        "focus on the merchant or counterparty name.\n"
        "- If the merchant is unknown or not inferable, use 'other'.\n"
        "- Descriptions may be in Italian or English.\n\n"
        "Examples:\n"
        f"{examples}\n\n"
        'Answer ONLY in JSON: {"category": "<code>"}.'
    )


def _ask(model: str, system: str, description: str) -> str | None:
    payload = {
        "model": model,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Description: {description}"},
        ],
    }
    req = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read())
        content = body["message"]["content"]
        return json.loads(content).get("category")
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] LLM error: {exc}")
        return None


def uncertain_rows(
    model: Any, rows: list[tuple[str, str]], margin: float
) -> list[tuple[str, str, float]]:
    """The ``(description, source)`` rows whose kNN vote margin is below
    ``margin``, most contested first. One encode for the whole batch."""
    if not rows:
        return []
    preds = model.predict_batch([build_text(d, s) for d, s in rows])
    out = [
        (d, s, float(p.margin))
        for (d, s), p in zip(rows, preds, strict=True)
        if p is not None and p.margin < margin
    ]
    out.sort(key=lambda r: r[2])
    return out


def _load_model() -> Any:
    if not MODEL_PATH.exists():
        return None
    from cashato.ml.model import EmbeddingKNN

    return EmbeddingKNN.load(MODEL_PATH)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--limit", type=int, default=1000, help="max distinct descriptions to label")
    ap.add_argument(
        "--margin",
        type=float,
        default=DEFAULT_MARGIN,
        help="also label model-resolved rows whose kNN vote margin is below this "
        "(0 disables; needs models/latest.joblib)",
    )
    args = ap.parse_args()

    cat = Categorizer.load()
    valid = set(cat.categories) | {cat.default}
    system = _prompt(cat)
    engine = get_engine()

    # 1. the long tail: distinct descriptions unresolved by the fast-path
    with engine.connect() as conn:
        tail = list(
            conn.execute(
                text(
                    "SELECT DISTINCT description, source FROM silver.transactions "
                    "WHERE category = :d LIMIT :lim"
                ),
                {"d": cat.default, "lim": args.limit},
            ).tuples()
        )
        # 2. the contested rows: what the model decided on a split vote
        model_rows = (
            list(
                conn.execute(
                    text(
                        "SELECT DISTINCT description, source FROM silver.transactions "
                        "WHERE category_source = 'model'"
                    )
                ).tuples()
            )
            if args.margin > 0
            else []
        )

    contested: list[tuple[str, str, float]] = []
    if model_rows:
        knn = _load_model()
        if knn is None:
            print(f"No {MODEL_PATH}: labeling the long tail only (train first for the margin pass)")
        else:
            contested = uncertain_rows(knn, model_rows, args.margin)

    budget = max(args.limit - len(tail), 0)
    rows = tail + [(d, s) for d, s, _ in contested[:budget]]
    print(
        f"To label: {len(rows)} distinct descriptions (model {args.model}): "
        f"{len(tail)} on '{cat.default}' + {len(rows) - len(tail)} contested "
        f"(margin < {args.margin}, {len(contested)} found)"
    )
    labeled = 0
    with engine.begin() as conn:
        for i, (descr, src) in enumerate(rows, 1):
            code = _ask(args.model, system, descr)
            if code not in valid:
                continue
            conn.execute(
                text(
                    """
                    INSERT INTO gold.training_labels (text_norm, category, source, confidence)
                    VALUES (:t, :c, 'llm', 0.75)
                    ON CONFLICT (text_norm, source) DO UPDATE SET category = EXCLUDED.category
                    """
                ),
                {"t": build_text(descr, src), "c": code},
            )
            labeled += 1
            if i % 50 == 0:
                print(f"  {i}/{len(rows)} ... labeled {labeled}")
    print(f"LLM labels saved to gold.training_labels: {labeled}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
