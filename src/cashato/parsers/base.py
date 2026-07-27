"""Common schema and shared utilities used by every adapter.

Each adapter (revolut, trade_republic, intesa) produces ``Transaction`` objects
conforming to the normalized schema. Amounts are always ``Decimal`` and
**signed** (negative = outflow, positive = inflow).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import cache


@cache
def _known_sources() -> frozenset[str]:
    # Deferred import: the registry imports the adapter modules, which import
    # this module — resolving it lazily (at first validation, well after import)
    # breaks that cycle. Sources are the auto-discovered adapter module names.
    from cashato.parsers.registry import SOURCE_NAMES

    return frozenset(SOURCE_NAMES)


class MoneyParseError(ValueError):
    """Raised when a monetary string cannot be interpreted."""


def parse_money(
    raw: str | Decimal | int | float,
    *,
    thousands_sep: str = ",",
    decimal_sep: str = ".",
) -> Decimal:
    """Parse a monetary string into a signed ``Decimal``.

    Handles currency symbols (``€``, ``EUR`` ...), thousands separators and the
    sign (both ASCII ``-`` and unicode ``−``). Numeric formats differ by source:

    - Revolut (US):  ``"€1,281.64"``  -> thousands=',', decimal='.'
    - Intesa (IT):   ``"1.281,64"``   -> thousands='.', decimal=','

    Never use ``float``: the value stays ``Decimal`` throughout the pipeline.
    """
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, int):
        return Decimal(raw)
    if isinstance(raw, float):  # defensive: should never happen
        raise MoneyParseError("float is not allowed for monetary amounts")

    s = str(raw).strip()
    if not s or s.upper() in {"N/A", "NA", "-", "—"}:
        raise MoneyParseError(f"empty or non-numeric amount: {raw!r}")

    # Normalize the sign (unicode minus -> ascii)
    s = s.replace("−", "-")

    # Drop everything except digits, separators, '-' and parens BEFORE reading
    # the sign, so a minus or an accounting paren sitting after a currency
    # symbol ("€-5.00", "€(5.00)", "EUR -5,00") is leading again once the
    # symbol is gone. A minus left INSIDE the digits is garbage and fails
    # parsing instead of being silently dropped.
    s = re.sub(r"[^0-9\-()" + re.escape(thousands_sep + decimal_sep) + r"]", "", s)
    # Accounting negatives: (5.00)
    paren = s.startswith("(") and s.endswith(")")
    if paren:
        s = s[1:-1]
    negative = paren or s.startswith("-") or s.endswith("-")
    s = s.strip("-")
    # Remove thousands separators, normalize the decimal to '.'
    if thousands_sep:
        s = s.replace(thousands_sep, "")
    if decimal_sep and decimal_sep != ".":
        s = s.replace(decimal_sep, ".")

    if not s:
        raise MoneyParseError(f"no digits in amount: {raw!r}")

    try:
        value = Decimal(s)
    except InvalidOperation as exc:  # pragma: no cover - defensive
        raise MoneyParseError(f"invalid amount: {raw!r}") from exc

    return -value if negative and value > 0 else value


def normalize_desc(description: str) -> str:
    """Normalize a description (lowercase, strip accents, collapse whitespace,
    drop punctuation). Used both for dedup and as the model feature text."""
    if description is None:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(description))
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    lowered = ascii_only.lower()
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", lowered)
    return re.sub(r"\s+", " ", cleaned).strip()


# --- Account descriptor (which bank, which product, held with whom) ----------
#
# The account *id* stays exactly as it is: it is hashed into ``natural_key``, so
# renaming one would invalidate every key ever computed. What a statement tells
# us about an account — the bank, the product name, whether it is held jointly —
# is therefore DISPLAY metadata carried alongside, never part of the id.

INDIVIDUAL = "individual"
JOINT = "joint"

# Italian IBAN: IT + 2 check digits + 1 CIN + 5 ABI + 5 CAB + 12 account number
# = 27 characters. Statements print it grouped ("IT47 K030 6915 ..."), so the
# search pattern tolerates spaces between characters and the result is compacted
# before validation. Matching on the ORIGINAL text matters: compacting the whole
# page first would glue the "IBAN" label onto the number and destroy the word
# boundary the search relies on.
_IBAN_FIND_RE = re.compile(r"\bIT\d{2}(?:\s*[A-Z0-9]){23}", re.IGNORECASE)
_IBAN_IT_RE = re.compile(r"IT\d{2}[A-Z]\d{10}[A-Z0-9]{12}", re.IGNORECASE)


@dataclass
class AccountInfo:
    """What a statement says about one account. Everything but the id is optional
    — sources disclose wildly different amounts of metadata, and absent is normal."""

    account_id: str
    bank_name: str | None = None
    product: str | None = None
    #: ``INDIVIDUAL`` / ``JOINT`` when the document states it, else ``None``.
    #: ``None`` means "not disclosed", which is NOT the same as individual.
    holding_modality: str | None = None
    currency: str | None = None
    iban: str | None = None


#: Which of the row's two dates a source's declared balances follow. Fintech
#: statements (Revolut, Trade Republic) have one date, so the two coincide;
#: classic bank statements (Intesa — and most Italian banks, should adapters
#: follow) order and total by the BOOKING date, and reconciling their anchors
#: against value-date sums manufactures mirrored discrepancies around every
#: statement boundary.
VALUE_BASIS = "value"
BOOKING_BASIS = "booking"


@dataclass
class BalanceAnchor:
    """A balance the statement itself declares, anchored to a date.

    Semantics: the account balance AFTER every movement whose ``basis`` date
    (value or booking — a property of the SOURCE's statements, declared by the
    adapter) is ``<= balance_date``. Anchors are what reconciliation checks the
    transactions against — between two consecutive anchors the sum of the
    movements must equal the balance delta; when it does not, a parser lost or
    invented rows (or two files disagree). The statement's own numbers are the
    only ground truth we have for that.
    """

    account: str
    balance_date: date
    balance: Decimal
    currency: str = "EUR"
    basis: str = VALUE_BASIS

    def __post_init__(self) -> None:
        if not isinstance(self.balance, Decimal):
            raise TypeError(f"balance must be Decimal, got {type(self.balance).__name__}")
        if self.basis not in (VALUE_BASIS, BOOKING_BASIS):
            raise ValueError(f"invalid basis: {self.basis!r}")
        self.balance = self.balance.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def find_iban(text: str) -> str | None:
    """First Italian IBAN in ``text`` (statements print it spaced or unspaced)."""
    for m in _IBAN_FIND_RE.finditer(text or ""):
        candidate = re.sub(r"\s+", "", m.group(0)).upper()
        if _IBAN_IT_RE.fullmatch(candidate):
            return candidate
    return None


def abi_from_iban(iban: str | None) -> str | None:
    """The 5-digit ABI (bank) code embedded in an Italian IBAN."""
    if not iban:
        return None
    compact = re.sub(r"\s+", "", iban).upper()
    return compact[5:10] if _IBAN_IT_RE.fullmatch(compact) else None


def bank_from_iban(iban: str | None) -> str | None:
    """Bank name for an IBAN, via the ABI lookup in ``config/banks.yaml``.

    Deferred import: this module is the adapters' stdlib toolkit, and the config
    loader is only needed on this path.
    """
    abi = abi_from_iban(iban)
    if not abi:
        return None
    from cashato.config import bank_names

    return bank_names().get(abi)


# --- Account holder extraction -------------------------------------------
#
# Every statement PDF carries an addressee block laid out the same way:
#
#     MARIO ROSSI   <- the account holder
#     Via Roma 1    <- street
#     00100 Roma    <- CAP (5-digit Italian postal code) + city
#
# so one position-aware helper serves all three sources: anchor on the CAP line
# and walk two lines up, staying inside the same column. Column-scoping matters —
# a flat text extraction interleaves the facing column (Trade Republic puts the
# statement period on the holder's line, Intesa the whole left column).

#: Name-order convention of a source's documents. Each adapter declares its own
#: via ``NAME_ORDER``. This is a documented property of the statement *layout*,
#: not a guess about the name: which token is the surname is not derivable from
#: the string alone ("ROSSI MARIO" has a two-token surname).
GIVEN_FIRST = "given_first"  # "MARIO ROSSI"  — Revolut, Trade Republic
FAMILY_FIRST = "family_first"  # "ROSSI MARIO"  — Italian bank statements

_CAP_RE = re.compile(r"^\d{5}$")
# A name token: letters (incl. accents) plus the punctuation real names carry.
# Deliberately rejects digits, which is what tells a name line from a street one.
_NAME_TOKEN_RE = re.compile(r"^[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’.-]*$")


def _lines_in_column(words: list[dict], x_lo: float, x_hi: float) -> list[list[dict]]:
    """Group the words within the ``[x_lo, x_hi]`` column into lines, top-down.

    Filtering by column *before* grouping (not after) is what keeps a facing
    column's text out of the line.
    """
    kept = sorted((w for w in words if x_lo <= w["x0"] <= x_hi), key=lambda w: (w["top"], w["x0"]))
    lines: list[list[dict]] = []
    for w in kept:
        # Same line if the baselines are within a few points (PDF tops of words
        # on one visual line are not bit-identical).
        if lines and abs(w["top"] - lines[-1][0]["top"]) <= 3:
            lines[-1].append(w)
        else:
            lines.append([w])
    return lines


def addressee_from_words(
    words: list[dict],
    *,
    column_width: float = 250.0,
    column_tol: float = 15.0,
    max_top: float = 400.0,
) -> str | None:
    """Extract the account holder from a statement page's ``extract_words()``.

    Finds a line that *starts* with a CAP, then returns the line two above it in
    the same column, provided that line looks like a person's name (>= 2 tokens,
    no digits). Returns ``None`` when the page has no such block — CSV/XLSX
    exports carry no addressee at all, and that is not an error.
    """
    # Reading order: the addressee block is near the top, so the first CAP that
    # validates wins. Candidates that are not really a CAP (e.g. the bank's own
    # address in a header line) fail the checks below and are skipped.
    candidates = sorted(
        (w for w in words if w["top"] <= max_top and _CAP_RE.match(w["text"])),
        key=lambda w: (w["top"], w["x0"]),
    )
    for cap in candidates:
        x_lo = cap["x0"] - column_tol
        lines = _lines_in_column(words, x_lo, cap["x0"] + column_width)
        idx = next(
            (i for i, ln in enumerate(lines) if ln[0] is cap),
            None,  # the CAP is not the leftmost word of its line -> not an address
        )
        if idx is None or idx < 2:
            continue
        name_line = lines[idx - 2]
        tokens = [w["text"] for w in name_line]
        if len(tokens) >= 2 and all(_NAME_TOKEN_RE.match(t) for t in tokens):
            return " ".join(tokens)
    return None


def format_holder(raw: str) -> str:
    """Presentation form of a holder name.

    Statement headers are ALL CAPS; title-case those for display. A name that is
    already mixed-case is left alone — the source knows its own casing better
    than ``str.title()`` does.
    """
    name = " ".join(raw.split())
    return name.title() if name.isupper() else name


def person_key(holder: str) -> frozenset[str]:
    """Identity of a holder, independent of how a source writes the name.

    Sources disagree on word order — Revolut prints "MARIO ROSSI", an
    Italian statement "ROSSI MARIO" — so comparing the strings would
    report one person as two. Comparing the normalized token SET does not.
    """
    return frozenset(normalize_desc(holder).split())


def given_name(holder: str, name_order: str) -> str:
    """Best-effort first name, for greeting the user.

    Uses the source's declared ``NAME_ORDER`` rather than guessing: the given
    name is the first token where documents put it first, the last token where
    they put the surname first.
    """
    tokens = format_holder(holder).split()
    if not tokens:
        return ""
    return tokens[-1] if name_order == FAMILY_FIRST else tokens[0]


# --- Instrument leg (what a cash movement actually bought or sold) -----------
#
# Two levels of investment tracking coexist, because the sources support
# different amounts of it:
#
#   * CASH FLOW — how much left the account towards investing. Always knowable,
#     including for a plain bank transfer to an outside broker, where the
#     contents are simply not in our documents.
#   * POSITIONS — which instrument, how many units, at what price. Only when the
#     source discloses it (the Trade Republic export does; a bank transfer does
#     not).
#
# So this hangs off a Transaction as an OPTIONAL leg rather than being folded
# into the common schema: absent means "we know money was invested, not what
# in", which is a real and common state, not missing data.

BUY = "buy"
SELL = "sell"


@dataclass
class TradeLeg:
    """The instrument side of an investment movement."""

    #: Signed in units: positive when acquiring, negative when disposing, so a
    #: position is just the running sum.
    quantity: Decimal
    side: str = BUY
    isin: str | None = None
    instrument: str | None = None
    asset_class: str | None = None
    unit_price: Decimal | None = None

    def __post_init__(self) -> None:
        for name in ("quantity", "unit_price"):
            v = getattr(self, name)
            if v is not None and not isinstance(v, Decimal):
                raise TypeError(f"{name} must be Decimal, got {type(v).__name__}")
        # Keep the sign a function of the side, so callers cannot disagree with
        # themselves and quietly corrupt a position total.
        self.quantity = -abs(self.quantity) if self.side == SELL else abs(self.quantity)


@dataclass
class Transaction:
    """A normalized transaction row (the common schema)."""

    value_date: date
    booking_date: date
    description: str
    amount: Decimal
    currency: str
    account: str
    source: str
    # Canonical, language-neutral category code, assigned by the Categorizer.
    category: str | None = None
    # Raw native category from the source (Revolut/Intesa/Trade Republic): an
    # optional bootstrap signal for training, NOT used at runtime and NOT part
    # of the natural_key. Kept for transparency.
    native_category: str | None = None
    # Merchant Category Code (ISO 18245) when the source exposes it (e.g. the
    # Trade Republic CSV). Universal, high-precision signal for the category.
    mcc: str | None = None
    # Optional disambiguator for the natural_key: the occurrence index assigned
    # by assign_occurrence_keys (distinguishes genuinely identical operations on
    # the same day without breaking cross-format dedup).
    dedup_extra: str = ""
    # What this movement bought or sold, when the source says. NOT part of the
    # natural_key: the same purchase read from the PDF (no instrument detail)
    # and from the CSV (full detail) must still dedup to one movement.
    trade: TradeLeg | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise TypeError(f"amount must be Decimal, got {type(self.amount).__name__}")
        if self.source not in _known_sources():
            raise ValueError(f"invalid source: {self.source!r}")
        self.currency = self.currency.upper()
        # Quantize to 2 decimals (cents): different sources/formats may express
        # the same amount with different scale (e.g. CSV "1000.000000" vs PDF
        # "1000.00"); quantization keeps keys consistent.
        self.amount = self.amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @property
    def natural_key(self) -> str:
        """Idempotent, **format-independent** dedup key.

        Based on ``account + value_date + amount + occurrence index``. The
        description is NOT part of the key because it differs across formats
        (the same movement has different text in PDF vs CSV): this way the same
        movement imported from different sources/formats (or overlapping
        exports) yields the same key and is recognized as already reconciled.

        ``dedup_extra`` carries the occurrence index (see
        :func:`assign_occurrence_keys`) that distinguishes genuinely identical
        operations on the same day without breaking cross-format dedup.
        """
        parts = [
            self.account,
            # value_date (not booking): it is the date shared across the various
            # exports of the same source (for Intesa the 13-month export's
            # "booking date" matches the quarterly statements' value date).
            # For Revolut/Trade Republic the two dates coincide.
            self.value_date.isoformat(),
            format(self.amount, "f"),
            self.dedup_extra,
        ]
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @property
    def _occ_group(self) -> tuple:
        return (self.account, self.value_date.isoformat(), format(self.amount, "f"))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["amount"] = format(self.amount, "f")
        d["value_date"] = self.value_date.isoformat()
        d["booking_date"] = self.booking_date.isoformat()
        d["natural_key"] = self.natural_key
        if self.trade is not None:
            # asdict() recurses but leaves Decimals as Decimal, which json
            # cannot serialize — the NATS job payload goes through json.dumps.
            d["trade"] = {
                **d["trade"],
                "quantity": format(self.trade.quantity, "f"),
                "unit_price": (
                    format(self.trade.unit_price, "f")
                    if self.trade.unit_price is not None
                    else None
                ),
            }
        return d


def assign_occurrence_keys(transactions: list[Transaction]) -> list[Transaction]:
    """Assign the occurrence index (``dedup_extra``) to each transaction.

    Counts, in order, how many transactions share the same
    ``(account, value_date, amount)`` group and numbers them 1..n. Must be
    computed **per complete file/statement**: two different exports (PDF/CSV) or
    overlapping ones containing the same set of movements for a given day
    produce the same indices -> same ``natural_key`` -> automatic dedup.

    Every adapter calls this before returning its transactions.
    """
    seen: Counter[tuple] = Counter()
    for t in transactions:
        seen[t._occ_group] += 1
        t.dedup_extra = str(seen[t._occ_group])
    return transactions
