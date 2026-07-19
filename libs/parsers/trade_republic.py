"""Trade Republic adapter -- PDF statement (IT or EN).

The PDF has a table ``DATA | TIPO | DESCRIZIONE | IN ENTRATA | IN USCITA |
SALDO`` (English: ``DATE | TYPE | DESCRIPTION | INCOMING | OUTGOING | BALANCE``).
The amount appears as a single number: the **sign depends on the column**
(incoming = +, outgoing = -), so parsing is *position-aware* (word X
coordinates), language-robust.

Scope: deposits/withdrawals/card payments = cash flow; savings plans / securities
/ dividends / interest = ``investments`` category.

Layout notes:
- the date is split across lines (``31 gen`` above, ``2025`` below) in the DATA
  column; one transaction = a block starting with a "day+month" row;
- amounts in IT format (``1.000,00``) or EN (``1,000.00``) depending on language.
"""

from __future__ import annotations

import bisect
import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pdfplumber

from .base import Transaction, assign_occurrence_keys, parse_money

ACCOUNT = "trade_republic"
SOURCE = "trade_republic"
CURRENCY = "EUR"

# Mesi abbreviati IT + EN -> numero
_MONTHS = {
    "gen": 1,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "mag": 5,
    "may": 5,
    "giu": 6,
    "jun": 6,
    "lug": 7,
    "jul": 7,
    "ago": 8,
    "aug": 8,
    "set": 9,
    "sep": 9,
    "ott": 10,
    "oct": 10,
    "nov": 11,
    "dic": 12,
    "dec": 12,
}

# Column header labels (IT/EN) used to locate the table geometry
_H_ENTRATA = {"ENTRATA", "INCOMING"}
_H_USCITA = {"USCITA", "OUTGOING"}
_H_SALDO = {"SALDO", "BALANCE"}
# Must appear in the movements-table header, NOT in the summary box
# (which also has "IN ENTRATA / IN USCITA / SALDO FINALE").
_H_DESC = {"DESCRIZIONE", "DESCRIPTION"}

# Tipi/descrizioni che indicano investimenti (IT + EN)
_INVEST_RE = re.compile(
    r"savings plan|piano di accumulo|dividend|dividendo|interest|interess|"
    r"\betf\b|\bishares\b|buy order|sell order|acquisto titoli|vendita titoli|"
    r"\bisin\b|reinvest",
    re.IGNORECASE,
)
_ISIN_RE = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}[0-9]\b")

# Numero monetario IT (1.234,56) o EN (1,234.56)
_MONEY_IT = re.compile(r"^\d{1,3}(?:\.\d{3})*,\d{2}$|^\d+,\d{2}$")
_MONEY_EN = re.compile(r"^\d{1,3}(?:,\d{3})*\.\d{2}$|^\d+\.\d{2}$")


@dataclass
class _Cols:
    """X boundaries (right-edge) of the amount columns, derived from the header."""

    entrata: float
    uscita: float
    saldo: float

    @property
    def ent_usc_split(self) -> float:
        return (self.entrata + self.uscita) / 2

    @property
    def usc_sal_split(self) -> float:
        return (self.uscita + self.saldo) / 2

    def classify(self, x1: float) -> str:
        if x1 < self.ent_usc_split:
            return "entrata"
        if x1 < self.usc_sal_split:
            return "uscita"
        return "saldo"


def _group_lines(words: list[dict]) -> list[tuple[float, list[dict]]]:
    """Group words into visual rows (by rounded top coordinate)."""
    lines: dict[int, list[dict]] = defaultdict(list)
    for w in words:
        lines[round(w["top"])].append(w)
    return [(top, sorted(ws, key=lambda w: w["x0"])) for top, ws in sorted(lines.items())]


def _detect_columns(lines: list[tuple[float, list[dict]]]) -> _Cols | None:
    for _top, ws in lines:
        texts = {w["text"].upper(): w for w in ws}
        if not (_H_DESC & texts.keys()):
            continue  # skip the summary box, look for the movements header
        ent = next((texts[t] for t in _H_ENTRATA if t in texts), None)
        usc = next((texts[t] for t in _H_USCITA if t in texts), None)
        sal = next((texts[t] for t in _H_SALDO if t in texts), None)
        if ent and usc and sal:
            return _Cols(entrata=ent["x1"], uscita=usc["x1"], saldo=sal["x1"])
    return None


def _clean_num(tok: str) -> str:
    # pdfplumber sometimes glues the currency symbol to the number (e.g. "€20.508,48")
    return tok.replace("€", "").replace("£", "").replace("$", "").strip()


def _is_money(tok: str) -> bool:
    t = _clean_num(tok)
    return bool(_MONEY_IT.match(t) or _MONEY_EN.match(t))


def _to_decimal(tok: str) -> Decimal:
    t = _clean_num(tok)
    if _MONEY_EN.match(t) and not _MONEY_IT.match(t):
        return parse_money(t, thousands_sep=",", decimal_sep=".")
    return parse_money(t, thousands_sep=".", decimal_sep=",")


@dataclass
class _Block:
    day: int | None = None
    month: int | None = None
    year: int | None = None
    desc_tokens: list[tuple[float, float, str]] = None  # (top, x0, text)
    amount: Decimal | None = None
    amount_kind: str | None = None  # 'entrata' | 'uscita'
    saldo: Decimal | None = None

    def __post_init__(self):
        if self.desc_tokens is None:
            self.desc_tokens = []

    @property
    def complete(self) -> bool:
        return (
            self.day
            and self.month
            and self.year
            and self.amount is not None
            and self.amount_kind in ("entrata", "uscita")
        )

    def to_date(self) -> date:
        return date(self.year, self.month, self.day)

    def description(self) -> str:
        toks = sorted(self.desc_tokens, key=lambda t: (t[0], t[1]))
        return re.sub(r"\s+", " ", " ".join(t[2] for t in toks)).strip()


def _amounts_in_row(
    ws: list[dict], cols: _Cols
) -> tuple[Decimal | None, str | None, Decimal | None]:
    """Extract (amount, column, balance) from the amounts in the right columns.

    x0 gate so that foreign-currency amounts written in the description (e.g.
    "387,95 £" of a GBP payment) are not mistaken for the real amount.
    """
    amount = kind = saldo = None
    for w in ws:
        if _is_money(w["text"]) and w["x0"] >= cols.entrata - 45:
            k = cols.classify(w["x1"])
            val = _to_decimal(w["text"])
            if k == "saldo":
                saldo = val
            elif amount is None:
                amount, kind = val, k
    return amount, kind, saldo


def _parse_blocks(lines: list[tuple[float, list[dict]]], cols: _Cols) -> list[_Block]:
    """Build transactions by anchoring them to the amount+balance row.

    The date (day/month/year) may be split across lines in the DATA column: each
    date/description token is assigned to the nearest anchor (the row with a
    balance) by ``top``. Robust to vertical layout.
    """
    anchors: list[tuple[float, Decimal | None, str | None, Decimal]] = []
    for top, ws in lines:
        amount, kind, saldo = _amounts_in_row(ws, cols)
        if saldo is not None:
            anchors.append((top, amount, kind, saldo))
    if not anchors:
        return []

    anchor_tops = [a[0] for a in anchors]

    def nearest(top: float) -> int:
        i = bisect.bisect_left(anchor_tops, top)
        cand = [j for j in (i - 1, i) if 0 <= j < len(anchors)]
        return min(cand, key=lambda j: abs(anchor_tops[j] - top))

    data_tokens: dict[int, list[str]] = defaultdict(list)
    desc_tokens: dict[int, list[tuple[float, float, str]]] = defaultdict(list)
    for top, ws in lines:
        j = nearest(top)
        for w in ws:
            x0, tok = w["x0"], w["text"]
            if x0 < 100:
                data_tokens[j].append(tok)
            elif 100 <= x0 < cols.entrata - 45 and tok != "€":
                desc_tokens[j].append((top, x0, tok))

    blocks: list[_Block] = []
    for j, (_top, amount, kind, saldo) in enumerate(anchors):
        b = _Block(amount=amount, amount_kind=kind, saldo=saldo, desc_tokens=desc_tokens[j])
        for tok in data_tokens[j]:
            low = tok.lower()
            if re.fullmatch(r"(19|20)\d{2}", tok):
                b.year = int(tok)
            elif b.day is None and re.fullmatch(r"\d{1,2}", tok):
                b.day = int(tok)
            elif b.month is None and low in _MONTHS:
                b.month = _MONTHS[low]
        blocks.append(b)
    return blocks


def parse(path: str | Path) -> list[Transaction]:
    """Dispatch by format: CSV (transaction export) or PDF (statement)."""
    if str(path).lower().endswith(".csv"):
        return _parse_csv(path)
    return _parse_pdf(path)


def _parse_pdf(path: str | Path) -> list[Transaction]:
    cols: _Cols | None = None
    blocks: list[_Block] = []

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            lines = _group_lines(page.extract_words(keep_blank_chars=False))
            if cols is None:
                cols = _detect_columns(lines)
            if cols is None:
                continue
            blocks.extend(_parse_blocks(lines, cols))

    if cols is None:
        raise ValueError("Intestazione colonne non trovata: layout Trade Republic non riconosciuto")

    transactions: list[Transaction] = []
    for b in blocks:
        if not b.complete:
            continue
        d = b.to_date()
        amount = b.amount if b.amount_kind == "entrata" else -b.amount
        desc = b.description()
        transactions.append(
            Transaction(
                value_date=d,
                booking_date=d,
                description=desc,
                amount=amount,
                currency=CURRENCY,
                account=ACCOUNT,
                source=SOURCE,
            )
        )
    return assign_occurrence_keys(transactions)


def _csv_money(row: dict, key: str) -> Decimal:
    """Read an amount (US format) from a CSV column, 0 if empty."""
    s = (row.get(key) or "").strip()
    return parse_money(s, thousands_sep=",", decimal_sep=".") if s else Decimal("0")


def _parse_csv(path: str | Path) -> list[Transaction]:
    """Parsa il 'Transaction export' CSV di Trade Republic.

    Molto piu' strutturato del PDF: ``amount`` gia' firmato (formato US),
    ``date`` ISO, ``category``/``type`` per il perimetro investimenti. Le stesse
    operazioni del PDF: la dedup canonica (account+data+amount+occorrenza) le
    riconosce come gia' riconciliate, evitando il doppio conteggio.
    """
    transactions: list[Transaction] = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row.get("currency") or "").upper() != CURRENCY:
                continue
            amount_raw = (row.get("amount") or "").strip()
            if not amount_raw:
                continue

            # Net impact on the cash account = amount + fee + tax. The PDF
            # statement folds the order fee (e.g. -1 on buys) and tax (net
            # interest) into the amount; summing them here makes the CSV match
            # the PDF, so cross-format dedup recognizes the same operations.
            amount = _csv_money(row, "amount") + _csv_money(row, "fee") + _csv_money(row, "tax")
            d = date.fromisoformat(row["date"].strip())

            name = (row.get("name") or "").strip()
            desc_txt = (row.get("description") or "").strip()
            cp = (row.get("counterparty_name") or "").strip()
            parts = [p for p in (name or cp, desc_txt) if p]
            descr = " - ".join(dict.fromkeys(parts)) or (row.get("type") or "").strip()

            transactions.append(
                Transaction(
                    value_date=d,
                    booking_date=d,
                    description=descr,
                    amount=amount,
                    currency=CURRENCY,
                    account=ACCOUNT,
                    source=SOURCE,
                    native_category=(row.get("category") or "").strip() or None,
                    mcc=(row.get("mcc_code") or "").strip() or None,
                )
            )
    return assign_occurrence_keys(transactions)
