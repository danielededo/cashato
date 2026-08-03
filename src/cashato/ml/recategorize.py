"""In-place re-categorization of silver.transactions.

Applies the provider-agnostic chain (MCC -> model -> rules -> default) to the
rows already in the DB, without re-parsing files. Uses ``models/latest.joblib``
if present (ML layer), with a confidence threshold. Reports the drop in ``other``.

Usage:
    ./.venv/bin/python ml/recategorize.py --threshold 0.75
"""

from __future__ import annotations

import argparse

from sqlalchemy import text

from cashato.config import MODEL_DIR, setting
from cashato.db.db import get_engine
from cashato.ml.model import EmbeddingKNN
from cashato.parsers.categorize import Categorizer

MODEL_PATH = MODEL_DIR / "latest.joblib"


def main() -> int:
    ap = argparse.ArgumentParser()
    # The fallback mirrors the shipped settings.yaml value: a missing config
    # must not lower the bar the batch applies vs what live resolution uses.
    ap.add_argument(
        "--threshold", type=float, default=setting("categorization.model_threshold", 0.75)
    )
    args = ap.parse_args()

    model = EmbeddingKNN.load(MODEL_PATH) if MODEL_PATH.exists() else None
    print(f"Model: {'loaded (embedding kNN)' if model else 'absent (MCC + rules only)'}")
    cat = Categorizer.load(model=model, model_threshold=args.threshold)

    engine = get_engine()
    other_pct = (
        "SELECT count(*) FILTER (WHERE category=:d)::float/count(*) FROM silver.transactions"
    )
    with engine.begin() as conn:
        before = conn.execute(text(other_pct), {"d": cat.default}).scalar_one()
        rows = conn.execute(
            # A user correction is ground truth, and this tool runs AFTER a
            # retrain — re-resolving every row would silently overwrite the very
            # labels the retrain learned from. The in-cluster worker already
            # promises manual rows are untouched; this honours the same contract.
            text(
                "SELECT id, description, source, mcc FROM silver.transactions "
                "WHERE category_source IS DISTINCT FROM 'manual'"
            )
        ).all()
        # Categorize in BATCH (a single encode for all rows)
        results = cat.resolve_many([(r.description, r.source, r.mcc) for r in rows])
        conn.execute(
            # The manual predicate is repeated in the UPDATE on purpose (same
            # as the categorizer worker): a POST /feedback can flip a row to
            # 'manual' during the minutes this batch spends embedding, and an
            # id-only UPDATE would overwrite the fresh correction.
            text(
                "UPDATE silver.transactions SET category=:c, category_confidence=:cf, "
                "category_source=:s WHERE id=:id AND category_source IS DISTINCT FROM 'manual'"
            ),
            [
                {"c": res.code, "cf": res.confidence, "s": res.source, "id": r.id}
                for r, res in zip(rows, results, strict=True)
            ],
        )
        after = conn.execute(text(other_pct), {"d": cat.default}).scalar_one()

    print(f"Re-categorized {len(rows)} rows. other: {before * 100:.1f}% -> {after * 100:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
