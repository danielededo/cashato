"""Intesa Sanpaolo adapter -- quarterly PDF statements (multiple files).

Classic Italian bank-statement format:
- table ``Data Operazione | Data Valuta | Descrizione | Addebiti | Accrediti``;
- **two dates** (booking = operazione, value = valuta) in ``dd.mm.yyyy`` format;
- **no per-row balance** (only opening/closing balance) -> verification uses the
  page-1 totals;
- the **sign is reconstructed from the column**: debits (addebiti) = -, credits
  (accrediti) = + (position-aware parsing on X coordinates);
- **multi-line descriptions** (one operation spans several rows);
- balance / header / footer rows to be skipped.

Multiple quarterly files: each file is parsed individually; **quarter-boundary
dedup** happens via ``natural_key``. Occurrence index + value date keep genuinely
distinct same-day operations apart while allowing the same operation across files
to collapse (see base.assign_occurrence_keys).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

import pdfplumber

from .base import (
    BOOKING_BASIS,
    FAMILY_FIRST,
    AccountInfo,
    BalanceAnchor,
    Transaction,
    addressee_from_words,
    assign_occurrence_keys,
    find_iban,
    parse_money,
)

ACCOUNT = "intesa"
SOURCE = "intesa"
CURRENCY = "EUR"

# Content-detection marker groups. A file is Intesa if, for ANY group, ALL
# markers appear in its lowercased head text. Italian markers match the real
# document text — keep them in Italian.
# Both groups are Intesa-SPECIFIC on purpose: generic Italian banking vocabulary
# steals other banks' documents — an ING quarterly says "Estratto conto
# trimestrale", a Hype movements table has a "Data Contabile" column — and
# misrouting is silent: the wrong parser finds no table and returns 0 rows.
DETECTION: list[list[str]] = [
    # The quarterly statement never names the bank in a field, but its page-1
    # footer does ("App Intesa Sanpaolo Mobile", intesasanpaolo.com).
    ["intesa sanpaolo"],
    # 13-month export, PDF and XLSX alike: the filter-recap header of Intesa's
    # own web export. Those two files carry no IBAN, so the ABI cannot anchor
    # them — this is the specific string they do have.
    ["conti e carte"],
]

_DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
# Rows that are NOT movements (headers, balances, footers). Italian doc words.
# ANCHORED on purpose. Unanchored, these words matched anywhere in a row, so a
# real movement containing ordinary banking vocabulary was silently discarded:
# "PAGAMENTO ESTRATTO CONTO CARTA NEXI" (a card settlement) matched "estratto",
# and a transfer's continuation line carrying "IBAN IT.." was dropped from its
# description. Structural rows — balances, page footers, section titles — put
# these words FIRST, which is what distinguishes them from a description.
_SKIP_RE = re.compile(
    r"^(?:saldo|pagina|estratto|totale|segue|riporto|dettaglio|coordinate|iban)\b",
    re.IGNORECASE,
)

# X regions (calibrated on the Intesa layout)
_DESC_X0_MIN = 150
_AMOUNT_X0_MIN = 360  # amounts live in the right-hand columns
# IT amount format: "110,92" or "1.234,56"
_AMOUNT_TOK = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2}")
# These Intesa PDFs use a control char (e.g. \x19) as the thousands separator:
# "3\x19090,00" = 3090,00. It must be removed before parsing.
_CTRL_RE = re.compile(r"[\x00-\x1f]")


def _clean(text: str) -> str:
    return _CTRL_RE.sub("", text)


def _group_lines(words: list[dict]) -> list[tuple[float, list[dict]]]:
    from collections import defaultdict

    lines: dict[int, list[dict]] = defaultdict(list)
    for w in words:
        lines[round(w["top"])].append(w)
    return [(t, sorted(ws, key=lambda w: w["x0"])) for t, ws in sorted(lines.items())]


def _find_header(lines) -> tuple[float, float] | None:
    """Return (header_top, boundary_x) of the movements table, or None.

    boundary_x separates debits (left) from credits (right)."""
    for top, ws in lines:
        texts = {w["text"] for w in ws}
        if "Descrizione" in texts:
            accred = next((w for w in ws if w["text"].startswith("Accred")), None)
            if accred:
                return top, accred["x0"] - 30
    return None


def _parse_date(tok: str) -> date:
    d, m, y = tok.split(".")
    return date(int(y), int(m), int(d))


def _row_dates(ws) -> tuple[date, date] | None:
    op = [w for w in ws if w["x0"] < 70 and _DATE_RE.match(w["text"])]
    if not op:
        return None
    val = [w for w in ws if 85 <= w["x0"] < 150 and _DATE_RE.match(w["text"])]
    d_op = _parse_date(op[0]["text"])
    d_val = _parse_date(val[0]["text"]) if val else d_op
    return d_op, d_val


def _row_amount(ws, boundary: float) -> tuple[Decimal, str] | None:
    # Only well-formed IT amount tokens in the right columns; take the rightmost
    # one (the row's single amount: debit XOR credit).
    money = [
        (w, c)
        for w in ws
        for c in [_clean(w["text"])]
        if w["x0"] >= _AMOUNT_X0_MIN and _AMOUNT_TOK.fullmatch(c)
    ]
    if not money:
        return None
    w, c = max(money, key=lambda p: p[0]["x1"])
    val = parse_money(c, thousands_sep=".", decimal_sep=",")
    kind = "accredito" if w["x1"] >= boundary else "addebito"
    return abs(val), kind


@dataclass
class _Tx:
    booking_date: date
    value_date: date
    amount: Decimal
    kind: str
    desc: list[str] = field(default_factory=list)


# Italian statements address the holder surname-first: "ROSSI MARIO".
NAME_ORDER = FAMILY_FIRST

# Product label in the left column of page 1; the value sits on the NEXT
# left-column line ("Tipologia conto:" / "XME Conto"), not on the same one.
_PRODUCT_LABEL = "tipologia conto"
_LEFT_COL_MAX_X = 270


def extract_accounts(path: str | Path) -> list[AccountInfo]:
    """The single current account behind the statement.

    Intesa never spells its own name out — the quarterly statement carries the
    IBAN and nothing else identifying the bank — so we return the IBAN and let
    the shared ABI lookup resolve the name. The product ("XME Conto") is stated,
    and the holding modality is not: ``None`` means undisclosed, not individual.
    """
    if not str(path).lower().endswith(".pdf"):
        return []
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        head = page.extract_text() or ""
        # Scope to the left column: "Tipologia conto:" sits within a couple of
        # points of the addressee block on the right, so a whole-page line
        # grouping would splice the two together.
        left = [w for w in page.extract_words(keep_blank_chars=False) if w["x0"] < _LEFT_COL_MAX_X]
        lines = [" ".join(w["text"] for w in ws) for _top, ws in _group_lines(left)]

    product = None
    for i, text in enumerate(lines[:-1]):
        if text.lower().startswith(_PRODUCT_LABEL):
            product = lines[i + 1].strip() or None
            break
    return [
        AccountInfo(
            account_id=ACCOUNT, product=product, currency=CURRENCY, iban=find_iban(head)
        )
    ]


# "Saldo iniziale al 31.12.2025 ... +3.434,25 €" / "Saldo finale al 31.03.2026
# ... +2.101,22 €". "Iniziale al" names the LAST day of the previous quarter, so
# both anchor as end-of-that-date balances. The amount is SIGNED and sits in the
# right-hand columns; the same statement also restates the closing balance
# unsigned in the competenze recap at x~270, which the x-gate excludes.
_SALDO_RE = re.compile(r"^saldo\s+(?:iniziale|finale)\s+al\s+(\d{2}\.\d{2}\.\d{4})", re.IGNORECASE)
_SALDO_AMOUNT_X0 = 430
_SALDO_SIGNED = re.compile(r"[+-]?\d{1,3}(?:\.\d{3})*,\d{2}|[+-]?\d+,\d{2}")


def extract_balances(path: str | Path) -> list[BalanceAnchor]:
    """Opening/closing balance anchors from the quarterly statement.

    The 13-month exports (PDF and XLSX) are movement listings with no balances,
    so they contribute nothing. The quarterly repeats each balance (summary page,
    table row, closing recap) — the first well-formed occurrence per date wins,
    and consecutive quarters share their boundary anchor (finale Q1 == iniziale
    Q2), which the loader's upsert collapses.
    """
    if not str(path).lower().endswith(".pdf"):
        return []
    anchors: dict[date, Decimal] = {}
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for _top, ws in _group_lines(page.extract_words(keep_blank_chars=False)):
                joined = _clean(" ".join(w["text"] for w in ws))
                m = _SALDO_RE.match(joined)
                if not m:
                    continue
                # The amount fragments into several tokens (the control-char
                # thousands separator splits words): concatenate the right-hand
                # run and require one well-formed signed IT amount.
                raw = "".join(
                    _clean(w["text"]) for w in ws if w["x0"] >= _SALDO_AMOUNT_X0
                ).replace("€", "").strip()
                if not _SALDO_SIGNED.fullmatch(raw):
                    continue
                anchors.setdefault(
                    _parse_date(m.group(1)),
                    parse_money(raw, thousands_sep=".", decimal_sep=","),
                )
    return [
        # The statement orders and totals by BOOKING date (data contabile):
        # reconciling these anchors against value-date sums would show every
        # cross-quarter valuta as a pair of mirrored discrepancies.
        BalanceAnchor(
            account=ACCOUNT, balance_date=d, balance=v, currency=CURRENCY, basis=BOOKING_BASIS
        )
        for d, v in anchors.items()
    ]


def extract_holder(path: str | Path) -> str | None:
    """Account holder, from the quarterly statement's addressee block (right-hand
    column of page 1). ``None`` for the 13-month export (PDF or XLSX): it is a
    movement listing with a filter recap, and carries no addressee."""
    if not str(path).lower().endswith(".pdf"):
        return None
    with pdfplumber.open(path) as pdf:
        return addressee_from_words(pdf.pages[0].extract_words())


def parse(path: str | Path) -> list[Transaction]:
    """Dispatch by Intesa format/layout.

    - ``.xlsx`` -> 'Lista Operazioni' 13-month export;
    - PDF 'Lista movimenti' (13-month) vs quarterly statement: told apart by the
      first-page content.
    All use account ``intesa`` and the canonical dedup: operations present in both
    the quarterly statements and the 13-month export are recognized as already
    reconciled.
    """
    s = str(path).lower()
    if s.endswith(".xlsx"):
        return _parse_xlsx(path)
    with pdfplumber.open(path) as pdf:
        head = pdf.pages[0].extract_text() or ""
    if "Lista movimenti" in head or "DATA CONTABILE" in head:
        return _parse_operazioni_pdf(path)
    return _parse_trimestrale(path)


def _parse_trimestrale(path: str | Path) -> list[Transaction]:
    txs_raw: list[_Tx] = []

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            lines = _group_lines(page.extract_words(keep_blank_chars=False))
            header = _find_header(lines)
            if header is None:
                continue
            header_top, boundary = header

            current: _Tx | None = None
            for top, ws in lines:
                if top <= header_top:
                    continue
                joined = " ".join(w["text"] for w in ws)
                if _SKIP_RE.search(joined):
                    current = None
                    continue

                dates = _row_dates(ws)
                if dates:
                    amt = _row_amount(ws, boundary)
                    if amt is None:
                        current = None
                        continue
                    if amt[0] == 0:
                        # Year-end rate-summary lines ("TASSO CREDITORE 0%")
                        # carry dates and a 0,00 amount and slipped in as
                        # movements with description "0%". A zero amount is
                        # never a movement: no income, no expense, no transfer.
                        current = None
                        continue
                    current = _Tx(
                        booking_date=dates[0],
                        value_date=dates[1],
                        amount=amt[0],
                        kind=amt[1],
                        desc=[w["text"] for w in ws if _DESC_X0_MIN <= w["x0"] < _AMOUNT_X0_MIN],
                    )
                    txs_raw.append(current)
                elif current is not None:
                    current.desc.extend(
                        w["text"] for w in ws if _DESC_X0_MIN <= w["x0"] < _AMOUNT_X0_MIN
                    )

    transactions: list[Transaction] = []
    for t in txs_raw:
        amount = t.amount if t.kind == "accredito" else -t.amount
        desc = re.sub(r"\s+", " ", " ".join(_clean(x) for x in t.desc)).strip()
        transactions.append(
            Transaction(
                value_date=t.value_date,
                booking_date=t.booking_date,
                description=desc,
                amount=amount,
                currency=CURRENCY,
                account=ACCOUNT,
                source=SOURCE,
                category=None,
            )
        )
    # Centralized canonical dedup: the occurrence index on (account, value_date,
    # amount) dedups quarter boundaries, overlapping exports (quarterly vs
    # 13-month) and different formats alike.
    return assign_occurrence_keys(transactions)


# Signed IT amount (for the 13-month export with a single amount column)
_AMOUNT_SIGNED = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}|-?\d+,\d{2}")


def _parse_xlsx(path: str | Path) -> list[Transaction]:
    """'Lista Operazioni' 13-month XLSX export (amount already signed)."""
    import warnings

    import openpyxl

    # Scoped suppression: keep the ignore local to this load.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()  # read-only workbooks hold the file handle until closed

    hdr = None
    start = 0
    for i, r in enumerate(rows):
        vals = [str(c).strip() if c is not None else "" for c in r]
        if "Data" in vals and "Importo" in vals:
            hdr = {v.strip(): j for j, v in enumerate(vals) if v}
            start = i + 1
            break
    if hdr is None:
        return []

    txs: list[Transaction] = []
    for r in rows[start:]:
        if r is None or r[hdr["Data"]] is None:
            continue
        dcell = r[hdr["Data"]]
        d = dcell.date() if hasattr(dcell, "date") else _parse_date(str(dcell))
        val = r[hdr["Importo"]]
        if val is None or str(val).strip() == "":
            continue
        # openpyxl yields floats for numeric cells (Transaction quantizes to
        # 2 dp), but a TEXT-formatted cell keeps the Italian string
        # ("1.234,56"), which Decimal() cannot read.
        if isinstance(val, str):
            amount = parse_money(val, thousands_sep=".", decimal_sep=",")
        else:
            amount = Decimal(str(val))
        if amount == 0:
            # Rate-summary lines, as in the PDF paths: never a movement.
            continue
        currency = str(r[hdr["Valuta"]]).strip().upper() if hdr.get("Valuta") is not None else "EUR"
        if currency != CURRENCY:
            continue
        op = str(r[hdr["Operazione"]] or "") if "Operazione" in hdr else ""
        det = str(r[hdr["Dettagli"]] or "") if "Dettagli" in hdr else ""
        descr = re.sub(r"\s+", " ", f"{op} {det}").strip()
        cat_nat = str(r[hdr["Categoria"]] or "").strip() if "Categoria" in hdr else ""
        txs.append(
            Transaction(
                value_date=d,
                booking_date=d,
                description=descr,
                amount=amount,
                currency=CURRENCY,
                account=ACCOUNT,
                source=SOURCE,
                native_category=cat_nat or None,
            )
        )
    return assign_occurrence_keys(txs)


# A wrapped description line sits at most this far from its own anchor row;
# page-leading lines farther than this from the page's FIRST anchor are the
# cross-page tail of the previous movement. Measured on the real export:
# within-block pitch is ~7pt (~15 with a category line interleaved), the gap
# between blocks is >=18pt.
_OPERAZIONI_WRAP_MAX_DIST = 20.0


def _parse_operazioni_pdf(path: str | Path) -> list[Transaction]:
    """'Lista movimenti' 13-month PDF export (signed IMPORTO column).

    Each movement is a block CENTERED on its anchor row (the one carrying date
    and amount): the description starts on the line(s) ABOVE the anchor,
    continues inline, and wraps below — e.g. "Bonifico istantaneo da voi" ↑ /
    "disposto a favore di Mario" inline / "Rossi" ↓.

    Assembly: every description/category line is assigned to the NEAREST anchor
    on the page (by vertical distance); lines above a page's first anchor whose
    gap exceeds the wrap pitch belong to the previous page's last movement.
    Sorting the assigned lines by ``top`` restores natural reading order.
    """
    raw: list[dict] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            anchors: list[tuple[float, dict]] = []
            body: list[tuple[float, list[dict]]] = []  # non-anchor lines, in order
            for top, ws in _group_lines(page.extract_words(keep_blank_chars=False)):
                joined = " ".join(w["text"] for w in ws)
                if "IMPORTO" in joined and "CATEGORIA" in joined:
                    continue
                dtok = [w for w in ws if w["x0"] < 120 and _DATE_RE.match(_clean(w["text"]))]
                imp = [
                    w
                    for w in ws
                    if w["x0"] >= 480
                    and _AMOUNT_SIGNED.fullmatch(_clean(w["text"]).replace("€", "").strip())
                ]
                if dtok and imp:
                    amount = parse_money(
                        _clean(imp[-1]["text"]).replace("€", ""),
                        thousands_sep=".",
                        decimal_sep=",",
                    )
                    if amount == 0:
                        # Rate-summary lines ("0%"), same as in the quarterly
                        # statements: a zero amount is never a movement.
                        continue
                    cur = {
                        "d": _parse_date(_clean(dtok[0]["text"])),
                        "imp": amount,
                        # (top, tokens) so above/inline/below merge in reading order
                        "desc": [(top, [w["text"] for w in ws if 120 <= w["x0"] < 335])],
                        "cat": [(top, [w["text"] for w in ws if 390 <= w["x0"] < 480])],
                    }
                    anchors.append((top, cur))
                    raw.append(cur)
                else:
                    body.append((top, ws))

            for top, ws in body:
                desc = [w["text"] for w in ws if 120 <= w["x0"] < 335]
                cat = [w["text"] for w in ws if 390 <= w["x0"] < 480]
                if not desc and not cat:
                    continue
                target: dict | None = None
                if anchors:
                    nearest = min(anchors, key=lambda a: abs(a[0] - top))
                    first_top = anchors[0][0]
                    if top < first_top and (first_top - top) > _OPERAZIONI_WRAP_MAX_DIST:
                        # Too far above the page's first anchor to be its wrap:
                        # tail of the previous page's last movement — or, on the
                        # first page, front-matter to DROP (not glue to row one).
                        if len(raw) > len(anchors):
                            target = raw[-len(anchors) - 1]
                    else:
                        target = nearest[1]
                elif raw:
                    target = raw[-1]  # page with no anchors: pure continuation
                if target is not None:
                    target["desc"].append((top, desc))
                    target["cat"].append((top, cat))

    for t in raw:
        t["desc"] = [tok for _top, toks in sorted(t["desc"]) for tok in toks]
        t["cat"] = [tok for _top, toks in sorted(t["cat"]) for tok in toks]

    txs: list[Transaction] = []
    for t in raw:
        descr = re.sub(r"\s+", " ", " ".join(_clean(x) for x in t["desc"])).strip()
        cat_nat = re.sub(r"\s+", " ", " ".join(_clean(x) for x in t["cat"])).strip()
        txs.append(
            Transaction(
                value_date=t["d"],
                booking_date=t["d"],
                description=descr,
                amount=t["imp"],
                currency=CURRENCY,
                account=ACCOUNT,
                source=SOURCE,
                native_category=cat_nat or None,
            )
        )
    return assign_occurrence_keys(txs)
