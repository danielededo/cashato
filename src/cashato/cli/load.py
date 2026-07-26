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
from cashato.parsers.base import bank_from_iban
from cashato.parsers.categorize import Categorizer
from cashato.parsers.registry import (  # (auto-discovered)
    ACCOUNT_EXTRACTORS,
    ADAPTERS,
    HOLDER_EXTRACTORS,
)

# The loader only applies the deterministic fast-path (MCC + rules): lightweight,
# no torch/model dependency. ML categorization is a separate concern
# (ml/recategorize.py locally; a categorizer calling the KServe-served model in
# phase C). This keeps the etl-worker light and fast.
_CATEGORIZER = Categorizer.load()


def record_unsupported(
    path: Path, filename: str, bank: str | None, ambiguous: list[str] | None = None
) -> None:
    """Register a file no adapter can read, so it does not vanish silently.

    Without this the object is stored and the job is consumed, but nothing ever
    appears in ``/files`` — from the UI the upload simply did nothing. Recording
    it as ``failed`` with a useful reason is the difference between "cashato is
    broken" and "cashato does not support this bank yet".

    ``ambiguous`` distinguishes the two ways detection declines: nothing matched,
    versus several sources matched equally well and guessing would be a coin
    flip. They need different actions from the user, so they say different things.
    """
    if ambiguous:
        reason = (
            f"Ambiguous statement: its content matches {' and '.join(sorted(ambiguous))} "
            f"equally well, so the source was not guessed. Force it by re-uploading "
            f"with an explicit `source` (POST /api/v1/uploads), or make the parsers' "
            f"DETECTION markers more specific."
        )
    elif bank:
        reason = f"Statement appears to be from {bank}, which has no adapter yet."
    else:
        reason = "Unrecognized statement format: no adapter matched this file's content."
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO bronze.raw_files (source, filename, sha256, size_bytes, status, error)
                VALUES (:source, :filename, :sha256, :size, 'failed', :error)
                ON CONFLICT (sha256) DO UPDATE
                    SET status = 'failed', error = EXCLUDED.error, filename = EXCLUDED.filename
                """
            ),
            {
                "source": bank or "unknown",
                # `path` is the fetched temp copy (hash/size come from it); the
                # display name must be the one the user actually uploaded.
                "filename": filename,
                "sha256": sha256_of(path),
                "size": path.stat().st_size,
                "error": reason,
            },
        )


def _upsert_accounts(conn, path: Path, source: str) -> int:
    """Record what this document says about the accounts it covers.

    Never fatal, and never destructive: a statement that omits a field must not
    erase what an earlier one told us, so each column only moves from NULL to a
    value (COALESCE on the new value first, existing second). The bank name is
    resolved here rather than in the adapters — most statements do not name their
    own bank, but every one of them carries an IBAN, and the ABI inside it does.
    """
    extract = ACCOUNT_EXTRACTORS.get(source)
    if extract is None:
        return 0
    try:
        accounts = extract(path)
    except Exception:  # noqa: BLE001 - descriptive metadata, never blocks ingestion
        return 0

    for a in accounts:
        conn.execute(
            text(
                """
                INSERT INTO silver.accounts
                    (account_id, source, bank_name, product, holding_modality, currency, iban)
                VALUES (:id, :source, :bank, :product, :modality, :currency, :iban)
                ON CONFLICT (account_id) DO UPDATE SET
                    bank_name        = COALESCE(EXCLUDED.bank_name, accounts.bank_name),
                    product          = COALESCE(EXCLUDED.product, accounts.product),
                    holding_modality = COALESCE(EXCLUDED.holding_modality, accounts.holding_modality),
                    currency         = COALESCE(EXCLUDED.currency, accounts.currency),
                    iban             = COALESCE(EXCLUDED.iban, accounts.iban),
                    updated_at       = now()
                """
            ),
            {
                "id": a.account_id,
                "source": source,
                "bank": a.bank_name or bank_from_iban(a.iban),
                "product": a.product,
                "modality": a.holding_modality,
                "currency": a.currency,
                "iban": a.iban,
            },
        )
    return len(accounts)


def _account_holder(path: Path, source: str) -> str | None:
    """The holder named on the document, or ``None``.

    Never fatal: only PDFs carry an addressee block, and a layout the extractor
    does not recognize is not a reason to fail an otherwise good ingestion — the
    column simply stays empty.
    """
    extract = HOLDER_EXTRACTORS.get(source)
    if extract is None:
        return None
    try:
        return extract(path)
    except Exception:  # noqa: BLE001 - cosmetic metadata, never blocks ingestion
        return None


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

    # 2b. A parse that yields nothing is treated as a FAILURE, not as an empty
    #     success. Content detection is first-match-wins over marker strings, so
    #     a document can be routed to the wrong adapter; that adapter then finds
    #     none of its table headers and returns [] perfectly quietly, and the
    #     file would land as 'parsed' with 0 rows and no error — nothing to
    #     alert on, and indistinguishable from "this statement was empty".
    #     Catching it here covers every future collision, whatever the markers.
    if not txs:
        reason = (
            f"Parsed as '{source}' but produced 0 transactions. The file was most "
            f"likely routed to the wrong parser (its content matched '{source}' "
            f"detection markers); if it belongs to a supported bank, re-upload it "
            f"with an explicit `source` (POST /api/v1/uploads)."
        )
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE bronze.raw_files SET status = 'failed', error = :e, "
                    "rows_total = 0, rows_new = 0 WHERE id = :id"
                ),
                {"e": reason, "id": file_id},
            )
        print(reason)
        return 0

    # 3. Load into silver (dedup on natural_key) and finalize the file status.
    with engine.begin() as conn:
        _upsert_accounts(conn, path, source)
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
            if t.trade is not None:
                # Keyed by natural_key, so this inherits the movement's dedup:
                # re-reading the same purchase updates one row instead of
                # doubling a position. The PDF has no instrument detail, so when
                # the CSV arrives later it fills the gap in place.
                conn.execute(
                    text(
                        """
                        INSERT INTO silver.trades
                            (natural_key, quantity, side, isin, instrument, asset_class, unit_price)
                        VALUES (:k, :qty, :side, :isin, :instrument, :asset_class, :price)
                        ON CONFLICT (natural_key) DO UPDATE SET
                            quantity = EXCLUDED.quantity, side = EXCLUDED.side,
                            isin = EXCLUDED.isin, instrument = EXCLUDED.instrument,
                            asset_class = EXCLUDED.asset_class, unit_price = EXCLUDED.unit_price
                        """
                    ),
                    {
                        "k": t.natural_key,
                        "qty": t.trade.quantity,
                        "side": t.trade.side,
                        "isin": t.trade.isin,
                        "instrument": t.trade.instrument,
                        "asset_class": t.trade.asset_class,
                        "price": t.trade.unit_price,
                    },
                )

        conn.execute(
            text(
                "UPDATE bronze.raw_files SET status = 'parsed', rows_total = :n, "
                "rows_new = :new, account_holder = :holder WHERE id = :id"
            ),
            {
                "n": len(txs),
                "new": inserted,
                # Recorded once the file is known-good, next to its final status.
                "holder": _account_holder(path, source),
                "id": file_id,
            },
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
