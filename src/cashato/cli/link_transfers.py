"""Detect internal transfers between own accounts and tag both legs.

Cross-account, cross-file post-processing over silver. Idempotent: it resets
``transfer_group`` and recomputes; the pairing is order-independent, so the
groups are stable across runs. GOLD spend views exclude the tagged legs, so
internal transfers do not pollute totals.

The etl-worker calls :func:`relink_all` after every ingest that inserted rows —
the gold views depend on the tagging, so it cannot be a manual afterthought.

CLI usage (manual re-run):
    ./.venv/bin/python -m cashato.cli.link_transfers [--window 3]
"""

from __future__ import annotations

import argparse
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Engine

from cashato.config import setting
from cashato.db.db import get_engine
from cashato.transfers import Leg, find_pairs


def relink_all(
    engine: Engine | None = None, window_days: int | None = None
) -> tuple[int, Decimal, Decimal]:
    """Reset and recompute ``transfer_group`` over all of silver, in one
    transaction. Returns ``(pairs, volume_excluded, net_after)``."""
    engine = engine or get_engine()
    window = int(setting("transfers.window_days", 3)) if window_days is None else window_days
    net_sql = "SELECT coalesce(sum(amount), 0) FROM silver.transactions WHERE transfer_group IS NULL"
    with engine.begin() as conn:
        conn.execute(text("UPDATE silver.transactions SET transfer_group = NULL"))
        rows = conn.execute(
            text(
                "SELECT id, natural_key, value_date, amount, account, description "
                "FROM silver.transactions ORDER BY id"
            )
        ).all()
        legs = [
            Leg(r.id, r.natural_key, r.account, r.value_date, r.amount, r.description) for r in rows
        ]
        amount_by_id = {leg.id: leg.amount for leg in legs}
        pairs = find_pairs(legs, window_days=window)

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
    return len(pairs), moved, net_after


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=int(setting("transfers.window_days", 3)))
    args = ap.parse_args()

    n_pairs, moved, net_after = relink_all(window_days=args.window)
    print(f"Internal transfers detected: {n_pairs} pairs (window {args.window}d)")
    print(f"Internal volume excluded: {moved} EUR")
    print(f"Spend net (excl. internal transfers): {net_after} EUR")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
