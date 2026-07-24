"""CLI loader: parse a statement file and load it into bronze + silver.

Usage:
    ./.venv/bin/python load.py --source revolut <file.csv>

Idempotent:
- bronze.raw_files has UNIQUE(sha256): an already-loaded file is skipped
  (unless --force);
- silver.transactions has UNIQUE(natural_key): INSERT ... ON CONFLICT DO NOTHING,
  so re-loading does not duplicate (and dedups overlapping Intesa quarters).
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from sqlalchemy import text

from cashato.db.db import get_engine
from cashato.parsers.categorize import Categorizer
from cashato.parsers.registry import ADAPTERS  # (name -> parse, from config)

# The loader only applies the deterministic fast-path (MCC + rules): lightweight,
# no torch/model dependency. ML categorization is a separate concern
# (ml/recategorize.py locally; a categorizer calling the KServe-served model in
# phase C). This keeps the etl-worker light and fast.
_CATEGORIZER = Categorizer.load()


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load(path: Path, source: str, force: bool = False) -> int:
    if source not in ADAPTERS:
        raise SystemExit(f"unsupported source: {source} (available: {list(ADAPTERS)})")

    digest = sha256_of(path)
    engine = get_engine()

    # 1. Register the file as 'pending' up-front, so a parse failure is visible
    #    (status 'failed' + error) instead of leaving no trace. An already-parsed
    #    file is skipped (its rows are in silver): 0 new, unless --force.
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT id, status FROM bronze.raw_files WHERE sha256 = :s"),
            {"s": digest},
        ).first()
        if existing and existing.status == "parsed" and not force:
            print(f"File already loaded (id={existing.id}); 0 new. Use --force to re-read.")
            return 0
        file_id = conn.execute(
            text(
                """
                INSERT INTO bronze.raw_files
                    (source, filename, sha256, size_bytes, status)
                VALUES (:source, :filename, :sha256, :size, 'pending')
                ON CONFLICT (sha256) DO UPDATE
                    SET status = 'pending', filename = EXCLUDED.filename, error = NULL
                RETURNING id
                """
            ),
            {
                "source": source,
                "filename": path.name,
                "sha256": digest,
                "size": path.stat().st_size,
            },
        ).scalar_one()

    # 2. Parse; on failure mark the file 'failed' and re-raise.
    try:
        txs = ADAPTERS[source](path)
    except Exception as exc:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE bronze.raw_files SET status = 'failed', error = :e WHERE id = :id"),
                {"e": str(exc)[:1000], "id": file_id},
            )
        raise

    # 3. Load into silver (dedup on natural_key) and finalize the file status.
    with engine.begin() as conn:
        inserted = 0
        for t in txs:
            # Inline provider-agnostic categorization: MCC -> rules -> model.
            r = _CATEGORIZER.resolve(t.description, t.source, t.mcc)
            res = conn.execute(
                text(
                    """
                    INSERT INTO silver.transactions
                        (value_date, booking_date, description, amount, currency,
                         account, source, category, category_confidence,
                         category_source, native_category, mcc, natural_key, file_id)
                    VALUES
                        (:value_date, :booking_date, :description, :amount, :currency,
                         :account, :source, :category, :category_confidence,
                         :category_source, :native_category, :mcc, :natural_key, :file_id)
                    ON CONFLICT (natural_key) DO NOTHING
                    """
                ),
                {
                    "value_date": t.value_date,
                    "booking_date": t.booking_date,
                    "description": t.description,
                    "amount": t.amount,
                    "currency": t.currency,
                    "account": t.account,
                    "source": t.source,
                    "category": r.code,
                    "category_confidence": r.confidence,
                    "category_source": r.source,
                    "native_category": t.native_category,
                    "mcc": t.mcc,
                    "natural_key": t.natural_key,
                    "file_id": file_id,
                },
            )
            inserted += res.rowcount

        conn.execute(
            text(
                "UPDATE bronze.raw_files SET status = 'parsed', rows_total = :n, "
                "rows_new = :new WHERE id = :id"
            ),
            {"n": len(txs), "new": inserted, "id": file_id},
        )

    print(f"Source: {source} | file_id={file_id}")
    print(
        f"Parsed transactions: {len(txs)} | newly inserted: {inserted} | "
        f"duplicates skipped: {len(txs) - inserted}"
    )
    return inserted


def main() -> int:
    ap = argparse.ArgumentParser(description="Load a statement into bronze+silver")
    ap.add_argument("file", type=Path)
    ap.add_argument("--source", required=True, choices=list(ADAPTERS))
    ap.add_argument("--force", action="store_true", help="re-read even if the file already exists")
    args = ap.parse_args()

    if not args.file.exists():
        raise SystemExit(f"file not found: {args.file}")

    load(args.file, args.source, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
