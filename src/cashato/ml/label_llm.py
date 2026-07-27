"""Offline labeling with a local LLM (Ollama).

Generates **canonical** labels (in OUR taxonomy) for the descriptions the
fast-path leaves unresolved, so the training set does not depend on provider
categories. Fully local (privacy). Requires a running Ollama:
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

from sqlalchemy import text

from cashato.db.db import get_engine
from cashato.parsers.categorize import Categorizer, build_text

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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--limit", type=int, default=1000, help="max distinct descriptions to label")
    args = ap.parse_args()

    cat = Categorizer.load()
    valid = set(cat.categories) | {cat.default}
    system = _prompt(cat)
    engine = get_engine()

    # distinct descriptions unresolved by the fast-path (category=other/default)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT DISTINCT description FROM silver.transactions "
                "WHERE category = :d LIMIT :lim"
            ),
            {"d": cat.default, "lim": args.limit},
        ).all()

    print(f"To label: {len(rows)} distinct descriptions (model {args.model})")
    labeled = 0
    with engine.begin() as conn:
        for i, (descr,) in enumerate(rows, 1):
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
                {"t": build_text(descr), "c": code},
            )
            labeled += 1
            if i % 50 == 0:
                print(f"  {i}/{len(rows)} ... labeled {labeled}")
    print(f"LLM labels saved to gold.training_labels: {labeled}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
