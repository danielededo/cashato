"""Revolut adapter -- "consolidated statement" format.

The real Revolut CSV is NOT the flat format originally assumed, but a
consolidated statement with **multiple sections per currency** separated by
``---------``. Cash movements live in tables titled ``Transaction statement``
with header::

    Date, Description, Category, Money in/out, Balance, Tax withheld, Other taxes, Fees

Scope decisions:
- only **EUR sections** are processed (MAD/RON/GBP ignored);
- ``Money in/out`` is already signed -> maps directly to ``amount``;
- if ``Fees`` != 0 a **separate fee transaction** is emitted (traceability);
- no ``State`` column in this format -> no PENDING/REVERTED filtering.

Interest (savings) and crypto sections have different headers and belong to the
"investments/crypto" scope: handled separately.
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pdfplumber
from dateutil import parser as dateparser

from .base import (
    GIVEN_FIRST,
    INDIVIDUAL,
    JOINT,
    AccountInfo,
    Transaction,
    addressee_from_words,
    assign_occurrence_keys,
    find_iban,
    normalize_desc,
    parse_money,
)

SOURCE = "revolut"
CURRENCY = "EUR"

# Content-detection marker groups (was config/sources.yaml). A file is Revolut if,
# for ANY group, ALL markers appear in its lowercased head text.
DETECTION: list[list[str]] = [
    ["current accounts summaries"],
    ["revolut"],
    ["money in/out", "balance"],
]

# Header of the cash-movements table (first 4 stable columns)
_TX_HEADER_PREFIX = ["Date", "Description", "Category", "Money in/out"]
# Extracts the currency code from "Personal Account (EUR)" / "Joint Account (EUR)"
_CURRENCY_RE = re.compile(r"\(([A-Z]{3})\)")
_SEPARATOR = "---------"


def _account_id(account_label: str, currency: str) -> str:
    """Account identifier from the section name: 'Joint Account (EUR)' ->
    'revolut_joint_eur'. Distinguishes different accounts in the same currency."""
    label = _CURRENCY_RE.sub("", account_label)  # drop "(EUR)"
    slug = normalize_desc(label).replace("account", "").strip().replace(" ", "_")
    slug = slug or "eur"
    return f"revolut_{slug}_{currency.lower()}"


@dataclass
class RevolutRow:
    """Raw row of a movements table (for bronze/verification)."""

    line_no: int
    account: str
    currency: str
    date: date
    description: str
    category: str
    money_in_out: Decimal
    balance: Decimal | None
    fee: Decimal


def _parse_date(raw: str) -> date:
    return dateparser.parse(raw.strip()).date()


def _money_or_none(raw: str) -> Decimal | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return parse_money(raw)
    except Exception:
        return None


def iter_rows(path: str | Path) -> Iterator[RevolutRow]:
    """Iterate the rows of the EUR "Transaction statement" tables (CSV).

    State machine: tracks the current section currency and, when it finds the
    movements-table header inside an EUR section, emits data rows until the
    table ends.
    """
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        current_currency: str | None = None
        current_account: str | None = None
        in_tx_table = False
        for line_no, row in enumerate(reader):
            first = row[0].strip() if row else ""

            # Separator or empty row: closes any open table
            if not any(cell.strip() for cell in row) or first.startswith(_SEPARATOR):
                in_tx_table = False
                continue

            # New account section -> update currency and account id
            m = _CURRENCY_RE.search(first)
            if m and ("Account" in first or "Savings" in first or "Pocket" in first):
                current_currency = m.group(1)
                current_account = _account_id(first, current_currency)
                in_tx_table = False
                continue

            # Movements-table header
            if [c.strip() for c in row[:4]] == _TX_HEADER_PREFIX:
                in_tx_table = current_currency == CURRENCY
                continue

            if not in_tx_table:
                continue

            # Data row: the first column must be a valid date
            try:
                d = _parse_date(first)
            except (ValueError, OverflowError):
                # not a date -> the table has ended
                in_tx_table = False
                continue

            money = _money_or_none(row[3] if len(row) > 3 else "")
            if money is None:
                continue
            balance = _money_or_none(row[4] if len(row) > 4 else "")
            fee_raw = row[7] if len(row) > 7 else ""
            fee = _money_or_none(fee_raw) or Decimal("0")

            yield RevolutRow(
                line_no=line_no,
                account=current_account or _account_id("Account", CURRENCY),
                currency=current_currency or CURRENCY,
                date=d,
                description=(row[1] if len(row) > 1 else "").strip(),
                category=(row[2] if len(row) > 2 else "").strip(),
                money_in_out=money,
                balance=balance,
                fee=abs(fee),
            )


# --- PDF variant (same consolidated statement in PDF form) ---
# Columns by X coordinate (from the layout): Date<120 | Description 120-257 |
# Category 257-320 | Money in/out 320-380 | Balance 380-425 | ... | Fees >=520.
_PDF_MONEY = re.compile(r"^-?\d{1,3}(?:,\d{3})*\.\d{2}$|^-?\d+\.\d{2}$")


def _pdf_money(tok: str) -> Decimal | None:
    t = tok.replace("€", "").replace("−", "-").strip()
    if not _PDF_MONEY.match(t):
        return None
    return parse_money(t, thousands_sep=",", decimal_sep=".")


def iter_rows_pdf(path: str | Path) -> Iterator[RevolutRow]:
    """Iterate the EUR 'Transaction statement' movements in the PDF.

    Same semantics as :func:`iter_rows` (CSV): same ``RevolutRow`` ->
    same keys -> automatic dedup between CSV and PDF."""
    rows: list[RevolutRow] = []
    cur_account: str | None = None
    cur_cur: str | None = None
    in_tx = False
    buf: dict | None = None

    def flush() -> None:
        nonlocal buf
        if buf and buf.get("money") is not None:
            rows.append(
                RevolutRow(
                    line_no=buf["ln"],
                    account=buf["account"],
                    currency=buf["cur"],
                    date=buf["date"],
                    description=re.sub(r"\s+", " ", " ".join(buf["desc"])).strip(),
                    category=" ".join(buf["cat"]).strip(),
                    money_in_out=buf["money"],
                    balance=buf["bal"],
                    fee=abs(buf["fee"]) if buf["fee"] is not None else Decimal("0"),
                )
            )
        buf = None

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            lines: dict[int, list[dict]] = defaultdict(list)
            for w in page.extract_words(keep_blank_chars=False):
                lines[round(w["top"])].append(w)
            for top in sorted(lines):
                ws = sorted(lines[top], key=lambda w: w["x0"])
                joined = " ".join(w["text"] for w in ws)

                m = _CURRENCY_RE.search(joined)
                if m and ("Account" in joined or "Savings" in joined or "Pocket" in joined):
                    flush()
                    cur_cur = m.group(1)
                    cur_account = _account_id(joined.split("(")[0], cur_cur)
                    in_tx = False
                    continue
                texts = {w["text"] for w in ws}
                if {"Date", "Description", "Category"} <= texts:
                    flush()
                    in_tx = cur_cur == CURRENCY
                    continue
                if not in_tx:
                    continue

                date_toks = [w for w in ws if w["x0"] < 120]
                dt = None
                if date_toks:
                    try:
                        dt = dateparser.parse(" ".join(w["text"] for w in date_toks)).date()
                    except (ValueError, OverflowError, TypeError):
                        dt = None

                if dt:
                    flush()
                    buf = {
                        "ln": top,
                        "account": cur_account,
                        "cur": cur_cur,
                        "date": dt,
                        "desc": [],
                        "cat": [],
                        "money": None,
                        "bal": None,
                        "fee": None,
                    }
                    for w in ws:
                        x0, tok = w["x0"], w["text"]
                        if x0 < 120:
                            continue
                        if x0 < 257:
                            buf["desc"].append(tok)
                        elif x0 < 320:
                            buf["cat"].append(tok)
                        elif x0 < 380:
                            buf["money"] = (
                                buf["money"] if _pdf_money(tok) is None else _pdf_money(tok)
                            )
                        elif x0 < 425 and _pdf_money(tok) is not None:
                            buf["bal"] = _pdf_money(tok)
                        elif x0 >= 520 and _pdf_money(tok) is not None:
                            buf["fee"] = _pdf_money(tok)
                elif buf is not None:
                    buf["desc"].extend(w["text"] for w in ws if 120 <= w["x0"] < 257)
    flush()
    yield from rows


# Revolut addresses the statement "DANIELE ROSSI" (given name first).
NAME_ORDER = GIVEN_FIRST

# The consolidated statement opens each account with a titled block followed by
# a "Current account details" key/value table. Both the CSV and the PDF carry it,
# but the CSV has it as real cells — far more robust than reading it off a PDF.
_ACCT_TITLE_RE = re.compile(r"^((?:[A-Z][a-z]+ )*Account) \(([A-Z]{3})\)$")
_ACCT_FIELDS = {
    "holding modalities": "holding_modality",
    "financial institution name": "bank_name",
}
_IBAN_LABEL = "account number"


def extract_accounts(path: str | Path) -> list[AccountInfo]:
    """Per-account metadata from the consolidated statement CSV.

    Revolut labels everything we need: the block title gives the product and
    currency ("Joint Account (EUR)"), and the details table states the holding
    modality and the institution outright — no inference. The PDF renders the
    same table but as positioned text, so we only read the CSV and let the PDF
    contribute nothing rather than guess.
    """
    if not str(path).lower().endswith(".csv"):
        return []
    found: dict[str, AccountInfo] = {}
    current: AccountInfo | None = None
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            cells = [c.strip() for c in row]
            first = cells[0] if cells else ""
            m = _ACCT_TITLE_RE.match(first)
            if m:
                product, currency = m.group(1), m.group(2)
                acc_id = _account_id(product, currency)
                # Same account can open several blocks (cash, savings, crypto);
                # keep the first, which is the one carrying the details table.
                current = found.setdefault(
                    acc_id,
                    AccountInfo(account_id=acc_id, product=product, currency=currency),
                )
                continue
            if current is None or len(cells) < 2:
                continue
            label, value = first.lower(), cells[1]
            if not value:
                continue
            # First value wins throughout. An account's title is followed by its
            # own details table and THEN by sub-blocks (savings, crypto pockets)
            # that restate the fields for the underlying personal account — the
            # joint account's own block says "Joint", a later sub-block says
            # "Individual", and last-wins would silently mislabel it.
            field = _ACCT_FIELDS.get(label)
            if field == "holding_modality":
                if current.holding_modality is None:
                    current.holding_modality = JOINT if value.lower() == "joint" else INDIVIDUAL
            elif field:
                if getattr(current, field) is None:
                    setattr(current, field, value)
            elif label.startswith(_IBAN_LABEL) and current.iban is None:
                current.iban = find_iban(value)
    return list(found.values())


def extract_holder(path: str | Path) -> str | None:
    """Account holder, from the PDF addressee block. ``None`` for the CSV, whose
    header carries account/institution details but no addressee."""
    if not str(path).lower().endswith(".pdf"):
        return None
    with pdfplumber.open(path) as pdf:
        return addressee_from_words(pdf.pages[0].extract_words())


def parse(path: str | Path) -> list[Transaction]:
    """Dispatch by format: CSV or PDF (same consolidated statement)."""
    if str(path).lower().endswith(".pdf"):
        txs = _build(iter_rows_pdf(path))
    else:
        # CSV also carries savings-interest and crypto-sale sections (dedicated
        # category, kept but excluded from spend via the gold views).
        txs = _build(iter_rows(path)) + _parse_interest_csv(path) + _parse_crypto_csv(path)
    # Centralized canonical dedup (occurrence index per account/date/amount)
    return assign_occurrence_keys(txs)


def _build(rows: Iterator[RevolutRow]) -> list[Transaction]:
    """Build the normalized transactions from the cash-movement rows (CSV or PDF).

    Each row with ``Fees`` != 0 produces a separate fee transaction (negative
    amount) for traceability.
    """
    transactions: list[Transaction] = []
    for r in rows:
        transactions.append(
            Transaction(
                value_date=r.date,
                booking_date=r.date,
                description=r.description,
                amount=r.money_in_out,
                currency=CURRENCY,
                account=r.account,
                source=SOURCE,
                native_category=r.category or None,  # seed for the Categorizer
            )
        )
        if r.fee and r.fee != 0:
            transactions.append(
                Transaction(
                    value_date=r.date,
                    booking_date=r.date,
                    description=f"Fee: {r.description}",
                    amount=-r.fee,
                    currency=CURRENCY,
                    account=r.account,
                    source=SOURCE,
                    native_category="Fees",
                )
            )
    return transactions


def _iter_section(path: str | Path, title_marker: str, header_prefix: list[str]):
    """Yield the data rows of a CSV section: the block after a row containing
    ``title_marker`` and the header row starting with ``header_prefix``, up to a
    blank/separator row. Only within EUR sections."""
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        currency: str | None = None
        armed = in_table = False
        for row in reader:
            first = row[0].strip() if row else ""
            m = _CURRENCY_RE.search(first)
            if m and ("Account" in first or "Savings" in first or "Statements" in first):
                currency = m.group(1)
            if not any(cell.strip() for cell in row) or first.startswith(_SEPARATOR):
                armed = in_table = False
                continue
            if title_marker in first:
                armed = True
                continue
            if armed and [c.strip() for c in row[: len(header_prefix)]] == header_prefix:
                in_table = True
                armed = False
                continue
            if in_table:
                yield currency, row


def _parse_interest_csv(path: str | Path) -> list[Transaction]:
    """Savings interest receipts -> inflows (dedicated 'investments' via rules)."""
    txs: list[Transaction] = []
    for currency, row in _iter_section(
        path, "only interest receipt", ["Date", "Description", "Gross rate"]
    ):
        if currency and currency != CURRENCY:
            continue
        net = _money_or_none(row[7] if len(row) > 7 else "")
        if net is None or net == 0:
            continue
        try:
            d = dateparser.parse(row[0].strip()).date()
        except (ValueError, OverflowError, TypeError):
            continue
        txs.append(
            Transaction(
                value_date=d,
                booking_date=d,
                description=(row[1] if len(row) > 1 else "Savings interest").strip(),
                amount=net,
                currency=CURRENCY,
                account="revolut_savings_eur",
                source=SOURCE,
                native_category="Interest",
            )
        )
    return txs


def _crypto_sale_value(cell: str) -> Decimal | None:
    # "Value (of Sale, of Purchase)" looks like "+ €1.99, - €2.02"; take the sale (+)
    for part in cell.split(","):
        if "+" in part:
            return _money_or_none(part)
    return None


def _parse_crypto_csv(path: str | Path) -> list[Transaction]:
    """Crypto sales -> realized proceeds tagged 'crypto' (kept, excluded from spend)."""
    txs: list[Transaction] = []
    for _currency, row in _iter_section(
        path, "only sales", ["Date (of Sale, of Purchase)"]
    ):
        if len(row) < 6:
            continue
        value = _crypto_sale_value(row[5])
        if value is None or value == 0:
            continue
        try:
            d = dateparser.parse(row[0].split(",")[0].strip(), dayfirst=True).date()
        except (ValueError, OverflowError, TypeError):
            continue
        symbol = (row[1] if len(row) > 1 else "").strip()
        txs.append(
            Transaction(
                value_date=d,
                booking_date=d,
                description=f"Crypto sale {symbol}".strip(),
                amount=value,
                currency=CURRENCY,
                account="revolut_crypto",
                source=SOURCE,
                native_category="Crypto",
            )
        )
    return txs
