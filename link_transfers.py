"""Detect internal transfers between own accounts and tag both legs.

Cross-account, cross-file post-processing over silver (runs after loading, not
per-file). Idempotent: it resets ``transfer_group`` and recomputes. GOLD spend
views exclude the tagged legs, so internal transfers no longer pollute totals.

Usage:
    ./.venv/bin/python link_transfers.py [--window 3]
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db.db import get_engine  # noqa: E402
from libs.config import setting  # noqa: E402
from libs.transfers import Leg, find_pairs  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=int(setting("transfers.window_days", 3)))
    args = ap.parse_args()

    engine = get_engine()
    net_sql = (
        "SELECT sum(amount) FROM silver.transactions WHERE transfer_group IS NULL"
    )
    with engine.begin() as conn:
        conn.execute(text("UPDATE silver.transactions SET transfer_group = NULL"))
        rows = conn.execute(
            text(
                "SELECT id, natural_key, value_date, amount, account, description "
                "FROM silver.transactions"
            )
        ).all()
        legs = [
            Leg(r.id, r.natural_key, r.account, r.value_date, r.amount, r.description) for r in rows
        ]
        amount_by_id = {leg.id: leg.amount for leg in legs}
        pairs = find_pairs(legs, window_days=args.window)

        params = []
        for out_id, in_id, group in pairs:
            params.append({"id": out_id, "g": group})
            params.append({"id": in_id, "g": group})
        if params:
            conn.execute(
                text("UPDATE silver.transactions SET transfer_group = :g WHERE id = :id"),
                params,
            )
        moved = sum((abs(amount_by_id[out_id]) for out_id, _, _ in pairs), Decimal("0"))
        net_after = conn.execute(text(net_sql)).scalar_one()

    print(f"Internal transfers detected: {len(pairs)} pairs (window {args.window}d)")
    print(f"Internal volume excluded: {moved} EUR")
    print(f"Spend net (excl. internal transfers): {net_after} EUR")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
