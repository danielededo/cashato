"""Automatic detection of an uploaded file's source, by **content**.

The user uploads any file and the ``etl-worker`` picks the right adapter based on
content signatures (no filename guessing). If detection is uncertain the caller
can pass an explicit source override (see the ingest API).

Returns ``"revolut" | "trade_republic" | "intesa"`` or ``None`` if unknown.
"""

from __future__ import annotations

from pathlib import Path

# detection signatures come from the registry (config-driven, one per source)
from cashato.parsers.registry import detection_signatures


def _csv_head(path: Path, nbytes: int = 4096) -> str:
    with open(path, encoding="utf-8", errors="ignore") as f:
        return f.read(nbytes).lower()


def _pdf_head(path: Path) -> str:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        return (pdf.pages[0].extract_text() or "").lower()


def _xlsx_head(path: Path, max_rows: int = 25) -> str:
    import warnings

    import openpyxl

    warnings.filterwarnings("ignore")
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    parts: list[str] = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i > max_rows:
            break
        parts.extend(str(c) for c in row if c is not None)
    return " ".join(parts).lower()


def head_text(path: Path) -> str | None:
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            return _csv_head(path)
        if suffix == ".pdf":
            return _pdf_head(path)
        if suffix in (".xlsx", ".xls"):
            return _xlsx_head(path)
    except Exception:
        return None
    return None


def detect_source(path: str | Path) -> str | None:
    text = head_text(Path(path))
    if not text:
        return None
    for source, groups in detection_signatures():
        if any(all(marker in text for marker in group) for group in groups):
            return source
    return None


def identify_bank(path: str | Path) -> str | None:
    """Name the bank behind a file we have no adapter for.

    Knowing *which* bank a statement comes from and being able to *parse* it are
    separate problems: parsing needs code that knows the layout, but identifying
    only needs the IBAN, which every Italian statement carries. So a file from an
    unsupported bank can say "this looks like BPER Banca" instead of just
    "unrecognized source".
    """
    from cashato.parsers.base import bank_from_iban, find_iban

    text = head_text(Path(path))
    return bank_from_iban(find_iban(text)) if text else None
