"""Unified export of the normalized dataset.

Writes a date-ordered CSV with categories **localized** in the chosen language
(categories in the DB are language-neutral codes; the translation from
config/categorie.yaml is applied here). Adding a language = add labels in the
config file, no code change.

Usage:
    ./.venv/bin/python export.py --lang it
    ./.venv/bin/python export.py --lang en --out output/transactions_en.csv
"""

from __future__ import annotations

import argparse
import csv
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

from cashato.db.db import get_engine
from cashato.parsers.categorize import Categorizer

COLUMNS = [
    "value_date",
    "booking_date",
    "description",
    "amount",
    "currency",
    "account",
    "source",
    "category_code",
    "category",
    "native_category",
]


def export(out: Path, lang: str) -> int:
    cat = Categorizer.load()
    if lang not in cat.languages:
        raise SystemExit(f"language not available: {lang} (available: {cat.languages})")

    engine = get_engine()
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with engine.connect() as conn, open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        rows = conn.execute(
            text(
                """
                SELECT value_date, booking_date, description, amount, currency,
                       account, source, category, native_category
                FROM silver.transactions
                ORDER BY value_date, account, id
                """
            )
        )
        for r in rows:
            w.writerow(
                [
                    r.value_date,
                    r.booking_date,
                    r.description,
                    r.amount,
                    r.currency,
                    r.account,
                    r.source,
                    r.category,
                    cat.label(r.category, lang),
                    r.native_category or "",
                ]
            )
            n += 1
    return n


def summary(lang: str) -> None:
    cat = Categorizer.load()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT category, count(*) AS n, sum(amount) AS net
                FROM silver.transactions GROUP BY category ORDER BY sum(amount)
                """
            )
        ).all()
    print(f"\nPer-category summary (language: {lang}):")
    for r in rows:
        print(f"  {cat.label(r.category, lang):22} n={r.n:5d}  net={Decimal(r.net):>12.2f} EUR")


def main() -> int:
    ap = argparse.ArgumentParser(description="Unified transactions export")
    ap.add_argument("--lang", default="it", help="category label language (it, en, ...)")
    ap.add_argument("--out", type=Path, default=Path("output/transazioni.csv"))
    args = ap.parse_args()

    n = export(args.out, args.lang)
    print(f"Exported {n} transactions -> {args.out} (category language: {args.lang})")
    summary(args.lang)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
