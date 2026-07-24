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

    # Determine the sign: leading or trailing '-'
    negative = s.startswith("-") or s.endswith("-")
    # Drop everything except digits and the known separators
    s = re.sub(r"[^0-9" + re.escape(thousands_sep + decimal_sep) + r"]", "", s)
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
