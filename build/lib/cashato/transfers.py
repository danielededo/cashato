"""Detection of **internal transfers** between the user's own accounts.

A transfer between two owned accounts produces two legs (outflow -X on account
A, inflow +X on account B) that are NOT spending. We pair them and tag both legs
with a shared ``transfer_group`` so the GOLD views can exclude them from
income/expense.

Matching (safe, with guard): equal absolute amount, opposite sign, different
account, ``|value_date diff| <= window``; a candidate is accepted only if it is
**same-day** OR at least one leg's description contains a transfer hint. Greedy
1:1 assignment (closest date, then largest amount, then natural keys) so each
leg is paired once and the result is independent of input order.
The ``transfer_group`` id is a deterministic hash of the two legs' natural keys
(idempotent across re-runs).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from cashato.parsers.base import normalize_desc

# Bilingual transfer hints (IT + EN), matched on the normalized description.
_HINT_RE = re.compile(
    r"\b(bonific|giroconto|giro\b|sepa|transfer|top ?up|topup|ricarica|incoming|outgoing)",
    re.IGNORECASE,
)


@dataclass
class Leg:
    id: int
    natural_key: str
    account: str
    value_date: date
    amount: Decimal
    description: str


def _has_hint(text: str) -> bool:
    return bool(_HINT_RE.search(normalize_desc(text)))


def _group_id(a: str, b: str) -> str:
    raw = "|".join(sorted([a, b]))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def find_pairs(
    legs: list[Leg], window_days: int = 3, require_hint: bool = True
) -> list[tuple[int, int, str]]:
    """Return ``(out_id, in_id, transfer_group)`` for each detected internal
    transfer. ``require_hint``: if True, non-same-day candidates must carry a
    transfer hint in at least one leg (guard against coincidental amounts)."""
    outs = [x for x in legs if x.amount < 0]
    ins_by_amt: dict[Decimal, list[Leg]] = {}
    for x in legs:
        if x.amount > 0:
            ins_by_amt.setdefault(x.amount, []).append(x)

    candidates: list[tuple[int, Decimal, Leg, Leg]] = []
    for o in outs:
        for i in ins_by_amt.get(-o.amount, []):
            if i.account == o.account:
                continue
            gap = abs((i.value_date - o.value_date).days)
            if gap > window_days:
                continue
            if require_hint and gap != 0 and not (_has_hint(o.description) or _has_hint(i.description)):
                continue
            candidates.append((gap, -abs(o.amount), o, i))

    # closest date first, then largest amount; natural keys break residual ties
    # so the pairing does not depend on the caller's row order (idempotency).
    candidates.sort(key=lambda c: (c[0], c[1], c[2].natural_key, c[3].natural_key))
    used: set[int] = set()
    pairs: list[tuple[int, int, str]] = []
    for _gap, _amt, o, i in candidates:
        if o.id in used or i.id in used:
            continue
        used.add(o.id)
        used.add(i.id)
        pairs.append((o.id, i.id, _group_id(o.natural_key, i.natural_key)))
    return pairs
