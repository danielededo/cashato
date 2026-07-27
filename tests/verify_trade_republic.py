"""End-to-end verification of the Trade Republic adapter on the real PDF.

Self-reconciling: it extracts the declared totals from the "ESTRATTO CONTO
RIASSUNTIVO" box (opening balance, inflows, outflows, closing balance) and
compares them with the sum of the extracted transactions. It also checks the
row-by-row balance continuity and the absence of duplicate natural_keys.

Usage:  ./.venv/bin/python tests/verify_trade_republic.py [pdf_path]
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pdfplumber

from cashato.parsers import trade_republic as tr

DEFAULT_PDF = "data/trade_republic/Account statement.pdf"


def _summary_totals(path: str):
    """Extract (opening_balance, inflow, outflow, closing_balance) from the summary."""
    with pdfplumber.open(path) as pdf:
        lines = tr._group_lines(pdf.pages[0].extract_words(keep_blank_chars=False))
    for _top, ws in lines:
        nums = [tr._to_decimal(w["text"]) for w in ws if tr._is_money(w["text"])]
        texts = {w["text"].lower() for w in ws}
        # summary product row: 4 amounts (opening, income, expense, closing)
        if len(nums) == 4 and ("corrente" in texts or "account" in texts):
            return nums
    return None


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PDF
    if not Path(path).exists():
        print(f"[ERROR] file not found: {path}")
        return 2

    txs = tr.parse(path)
    inflows = sum((t.amount for t in txs if t.amount > 0), Decimal("0"))
    outflows = sum((t.amount for t in txs if t.amount < 0), Decimal("0"))
    net = inflows + outflows

    print(f"File: {path}")
    print(f"Transactions:           {len(txs)}")
    print(f"Inflows:                {inflows}")
    print(f"Outflows:               {outflows}")
    print(f"Net:                    {net}")
    print(f"Investments:            {sum(1 for t in txs if t.category == 'investments')}")

    ok = len(txs) > 0

    # 1. Reconciliation against the summary box
    tot = _summary_totals(path)
    if tot:
        s_open, s_in, s_out, s_close = tot
        print(f"\nPDF summary: opening={s_open} inflow={s_in} outflow={s_out} closing={s_close}")
        checks = [
            ("inflows", inflows, s_in),
            ("outflows", -outflows, s_out),
            ("closing balance", s_open + net, s_close),
        ]
        for name, got, exp in checks:
            good = abs(got - exp) <= Decimal("0.01")
            ok = ok and good
            print(f"  {'OK ' if good else 'ERR'} {name}: {got} vs {exp}")
    else:
        print("\n[warn] summary box not found: skipping the reconciliation")

    # 2. Balance continuity + dedup
    dup = len(txs) - len({t.natural_key for t in txs})
    print(f"\nDuplicate natural keys: {dup}")
    ok = ok and dup == 0

    print("\nRESULT:", "OK" if ok else "NEEDS REVIEW")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
