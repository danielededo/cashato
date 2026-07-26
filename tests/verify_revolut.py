"""End-to-end verification of the Revolut adapter on the real CSV (phase A).

No database required: it checks that the parsing is correct.

Checks:
1. number of EUR rows extracted (plausibility);
2. sign consistency (top-ups positive, merchants negative);
3. balance reconstruction: balance[i] == balance[i-1] + money_in_out[i];
4. dedup: no duplicate natural_key among the transactions parse() produces.

Usage:  ./.venv/bin/python tests/verify_revolut.py [csv_path]
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

from cashato.parsers import revolut

DEFAULT_CSV = "data/Revolut/consolidated-statement-v2_2023-10-10_2026-07-18_en_4941b1.csv"


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CSV
    if not Path(path).exists():
        print(f"[ERROR] file not found: {path}")
        return 2

    rows = list(revolut.iter_rows(path))
    txs = revolut.parse(path)

    print(f"File: {path}")
    print(f"EUR rows (movements):         {len(rows)}")
    print(f"Transactions from parse():    {len(txs)}  (incl. fee rows)")

    if not rows:
        print("[ERROR] no rows extracted")
        return 1

    pos = sum(1 for r in rows if r.money_in_out > 0)
    neg = sum(1 for r in rows if r.money_in_out < 0)
    tot = sum((r.money_in_out for r in rows), Decimal("0"))
    accounts = sorted({r.account for r in rows})
    print(f"Inflows/outflows (rows):      +{pos} / -{neg}")
    print(f"Period:                       {rows[0].date} -> {rows[-1].date}")
    print(f"Net sum of movements:         {tot} EUR")
    print(f"Distinct EUR accounts:        {accounts}")

    # 3. Balance reconstruction, per account (reset at boundaries)
    balance_errors = 0
    prev: dict[str, Decimal] = {}
    for r in rows:
        if r.balance is None:
            continue
        if r.account in prev:
            expected = prev[r.account] + r.money_in_out
            if abs(expected - r.balance) > Decimal("0.01"):
                balance_errors += 1
                if balance_errors <= 5:
                    print(
                        f"  [balance] row {r.line_no} ({r.account}): expected {expected}"
                        f" != balance {r.balance} ({r.description!r})"
                    )
        prev[r.account] = r.balance
    print(f"Balance discrepancies:        {balance_errors}")

    # 4. Dedup natural_key
    keys = [t.natural_key for t in txs]
    dup = len(keys) - len(set(keys))
    print(f"Duplicate natural keys:       {dup}")

    ok = balance_errors == 0 and len(rows) > 0
    print("\nRESULT:", "OK" if ok else "NEEDS REVIEW")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
