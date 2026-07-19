"""M3 — In-place re-categorization of silver.transactions.

Applies the provider-agnostic chain (MCC -> model -> rules -> default) to the
rows already in the DB, without re-parsing files. Uses ``models/latest.joblib``
if present (ML layer), with a confidence threshold. Reports the drop in ``other``.

Usage:
    ./.venv/bin/python ml/recategorize.py --threshold 0.55
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.db import get_engine  # noqa: E402
from libs.config import setting  # noqa: E402
from libs.parsers.categorize import Categorizer  # noqa: E402
from ml.model import EmbeddingKNN  # noqa: E402

MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "latest.joblib"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--threshold", type=float, default=setting("categorization.model_threshold", 0.55)
    )
    args = ap.parse_args()

    model = EmbeddingKNN.load(MODEL_PATH) if MODEL_PATH.exists() else None
    print(f"Model: {'loaded (embedding kNN)' if model else 'absent (MCC + rules only)'}")
    cat = Categorizer.load(model=model, model_threshold=args.threshold)

    engine = get_engine()
    other_pct = (
        "SELECT count(*) FILTER (WHERE category='other')::float/count(*) FROM silver.transactions"
    )
    with engine.begin() as conn:
        before = conn.execute(text(other_pct)).scalar_one()
        rows = conn.execute(
            text("SELECT id, description, source, mcc FROM silver.transactions")
        ).all()
        # Categorize in BATCH (a single encode for all rows)
        results = cat.resolve_many([(r.description, r.source, r.mcc) for r in rows])
        conn.execute(
            text(
                "UPDATE silver.transactions SET category=:c, category_confidence=:cf, "
                "category_source=:s WHERE id=:id"
            ),
            [
                {"c": res.code, "cf": res.confidence, "s": res.source, "id": r.id}
                for r, res in zip(rows, results, strict=True)
            ],
        )
        after = conn.execute(text(other_pct)).scalar_one()

    print(f"Re-categorized {len(rows)} rows. other: {before * 100:.1f}% -> {after * 100:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
