"""Automatic detection of an uploaded file's source, by **content**.

The user uploads any file and the ``etl-worker`` picks the right adapter based on
content signatures (no filename guessing). If detection is uncertain the caller
can pass an explicit source override (see the ingest API).

**Registry order is not a tie-breaker.** Every source is scored and the most
specific match wins, where specific means "matched more markers". If two sources
tie at the top the file is reported as AMBIGUOUS (``None``) rather than resolved
by accident: an honest "I cannot tell, choose the bank yourself" beats a coin
flip the user never sees. See `demo/DETECTION_COLLISIONS.md`.
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

    # Scoped suppression: keep the ignore local to this load.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        parts: list[str] = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i > max_rows:
                break
            parts.extend(str(c) for c in row if c is not None)
        return " ".join(parts).lower()
    finally:
        wb.close()  # read-only workbooks hold the file handle until closed


def head_text(path: Path) -> str | None:
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            return _csv_head(path)
        if suffix == ".pdf":
            return _pdf_head(path)
        # .xls (the legacy binary format) is deliberately NOT here: uploads
        # reject it (settings.yaml allowed_extensions) and openpyxl cannot
        # read it, so accepting it in detection was a dead promise.
        if suffix == ".xlsx":
            return _xlsx_head(path)
    except Exception:
        return None
    return None


def detect_candidates(path: str | Path) -> list[tuple[str, int]]:
    """Every source whose markers match, with the size of its best match.

    The score is the number of markers in the matched group: a group naming the
    bank plus a layout string is a stronger claim than one generic word. Length
    is deliberately NOT used — "estratto conto" is longer than "revolut" and far
    less specific, so it would rank the wrong way round.
    """
    text = head_text(Path(path))
    if not text:
        return []
    scored: list[tuple[str, int]] = []
    for source, groups in detection_signatures():
        sizes = [len(g) for g in groups if all(marker in text for marker in g)]
        if sizes:
            scored.append((source, max(sizes)))
    return sorted(scored, key=lambda p: (-p[1], p[0]))


def detect_source(path: str | Path) -> str | None:
    """The one source this file belongs to, or ``None`` if unknown OR ambiguous.

    Returning ``None`` for an ambiguous file is the point: the caller records it
    with a reason and the user picks the bank, instead of the file being handed
    to whichever adapter happened to sort first.
    """
    candidates = detect_candidates(path)
    if not candidates:
        return None
    if len(candidates) > 1 and candidates[0][1] == candidates[1][1]:
        return None  # tie -> genuinely ambiguous, do not guess
    return candidates[0][0]


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
