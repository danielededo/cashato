"""Recurring-movement detection: subscriptions, salaries, rent, utility bills.

Pure functions over already-fetched transaction rows — no DB access, so the
detector unit-tests on synthetic series and the query-api can run it on the
fly (a personal dataset is thousands of rows, not millions; recomputing per
request keeps the pipeline free of derived tables that could go stale).

A movement is *recurring* when the same merchant charges (or pays) at a steady
rhythm. Both halves matter and both are checked:

- **same merchant** — grouping key = the normalized description with every
  number dropped. Dates, receipt numbers and amounts embedded in the text
  change per occurrence; the merchant words do not.
- **steady rhythm** — the median gap between consecutive occurrence dates must
  land in one of the known cadence windows, and most individual gaps must sit
  inside that window too. This is what separates Netflix from the supermarket
  you happen to visit most weeks: both have many occurrences, only one keeps
  the beat.

Amounts are judged LOCALLY, not globally: a salary drifts from 500 to 2200
over a career and stays one relationship, so a global min-max spread would
reject exactly the series that matter most. What recurring amounts do not do
is jump around between consecutive occurrences — so the gate is the share of
consecutive pairs within a factor of 1.5 of each other, waived only when the
dates are near-perfectly regular (utility bills vary with the season but
arrive like clockwork). Date-irregular AND amount-inconsistent groups
(ordinary shopping) never qualify.

"Active" is judged against the newest date in the dataset, not the wall
clock: the data ends when the last statement does, and a subscription is not
lapsed just because no statement has been uploaded since.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

from cashato.parsers.base import normalize_desc

# (code, nominal gap in days, window low, window high) — ordered, non-overlapping.
# Windows are loose on purpose: a monthly charge drifts with month lengths and
# weekends, an annual one with leap years and renewal grace periods.
CADENCES: tuple[tuple[str, int, int, int], ...] = (
    ("weekly", 7, 5, 10),
    ("monthly", 30, 24, 38),
    ("bimonthly", 61, 50, 75),
    ("quarterly", 91, 78, 110),
    ("semiannual", 182, 155, 220),
    ("yearly", 365, 330, 430),
)

MIN_OCCURRENCES = 3
# Share of gaps that must fall inside the cadence window.
MIN_REGULARITY = 0.7
# Consecutive day-totals must be within this factor of each other to count as
# consistent, and at least MIN_CONSISTENCY of the pairs must be — unless the
# dates are near-perfectly regular (bills vary, but arrive on schedule).
PAIR_FACTOR = Decimal("1.5")
MIN_CONSISTENCY = 0.7
STRICT_REGULARITY = 0.9
# A group is still active while the silence after its last occurrence is
# shorter than this multiple of the cadence's nominal gap.
ACTIVE_SLACK = 1.6

_AVG_MONTH_DAYS = Decimal("30.44")
_CENT = Decimal("0.01")

# The categories gold's spend views exclude as wealth changing form rather
# than consumption. A monthly ETF plan is a genuine recurrence worth listing,
# but it must not inflate the recurring-SPEND total.
ASSET_CATEGORIES = frozenset({"investments", "crypto"})

_NUM_RE = re.compile(r"[0-9]+")
# Month names are calendar tokens — the same class as digits. A payroll
# causale names the month it settles ("saldo cedolino giugno"), which would
# otherwise give every occurrence of one salary a unique key. Full names
# only: abbreviations collide with real words ("mar", "ago").
_MONTH_RE = re.compile(
    r"\b(?:gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre"
    r"|ottobre|novembre|dicembre|january|february|march|april|may|june|july"
    r"|august|september|october|november|december)\b"
)


def recurrence_key(description: str) -> str:
    """Merchant key: normalized description with numbers and month names
    dropped, and runs of single-letter tokens joined.

    The join makes punctuation-variant acronyms one token — a statement writes
    the same employer as "ACME SPA" one month and "ACME S.P.A." the next, and
    normalization turns the latter into "s p a". Single letters standing alone
    are kept: only a RUN of them spells an acronym.
    """
    text = _MONTH_RE.sub(" ", _NUM_RE.sub(" ", normalize_desc(description)))
    tokens = re.sub(r"\s+", " ", text).strip().split(" ")
    out: list[str] = []
    run: list[str] = []
    for tok in [*tokens, ""]:  # sentinel flushes the last run
        if len(tok) == 1:
            run.append(tok)
            continue
        if len(run) > 1:
            out.append("".join(run))
        else:
            out.extend(run)
        run = []
        if tok:
            out.append(tok)
    return " ".join(out)


@dataclass
class Recurring:
    """One detected recurring movement group."""

    key: str
    description: str  # richest raw description observed in the group
    category: str | None
    accounts: list[str]
    cadence: str
    n_occurrences: int
    first_date: date
    last_date: date
    amount: Decimal  # signed median amount
    amount_min: Decimal
    amount_max: Decimal
    monthly_equivalent: Decimal  # signed, normalized to a 30.44-day month
    regularity: float  # share of gaps inside the cadence window
    active: bool
    next_expected: date | None  # last + median gap, only while active


def _cadence_for(gap: float) -> tuple[str, int, int, int] | None:
    for c in CADENCES:
        if c[2] <= gap <= c[3]:
            return c
    return None


def _qualify(members: list[dict], horizon: date) -> Recurring | None:
    """Build a Recurring from one candidate group, or None if it has no rhythm."""
    # One occurrence per day: two same-day charges by one merchant are one
    # billing event (split payments), not a shorter cadence.
    by_day: dict[date, list[dict]] = {}
    for m in members:
        by_day.setdefault(m["value_date"], []).append(m)
    if len(by_day) < MIN_OCCURRENCES:
        return None
    days = sorted(by_day)
    gaps = [(b - a).days for a, b in zip(days, days[1:], strict=False)]

    med_gap = median(gaps)
    cadence = _cadence_for(med_gap)
    if cadence is None:
        return None
    code, nominal, lo, hi = cadence
    regularity = sum(1 for g in gaps if lo <= g <= hi) / len(gaps)
    if regularity < MIN_REGULARITY:
        return None

    # Daily totals IN DAY ORDER (consecutive pairs are compared below), so a
    # split charge counts once at its full size.
    day_totals = [
        sum((m["amount"] for m in by_day[d]), Decimal(0)).quantize(_CENT) for d in days
    ]
    amt_med = median(day_totals).quantize(_CENT)
    amt_min, amt_max = min(day_totals), max(day_totals)
    if amt_med == 0:
        return None
    consistent = 0
    for a, b in zip(day_totals, day_totals[1:], strict=False):
        lo, hi = min(abs(a), abs(b)), max(abs(a), abs(b))
        if lo > 0 and hi / lo <= PAIR_FACTOR:
            consistent += 1
    if consistent / (len(day_totals) - 1) < MIN_CONSISTENCY and regularity < STRICT_REGULARITY:
        return None

    active = (horizon - days[-1]).days <= nominal * ACTIVE_SLACK
    # The richest description names the merchant best (same convergence rule
    # silver uses); categories follow the most recent occurrence.
    richest = max(members, key=lambda m: len(m["description"]))
    latest = max(members, key=lambda m: m["value_date"])
    return Recurring(
        key=recurrence_key(richest["description"]),
        description=richest["description"],
        category=latest.get("category"),
        accounts=sorted({m["account"] for m in members}),
        cadence=code,
        n_occurrences=len(days),
        first_date=days[0],
        last_date=days[-1],
        amount=amt_med,
        amount_min=amt_min,
        amount_max=amt_max,
        # str() so a fractional median gap (even gap count) enters Decimal
        # exactly, not through a float's binary expansion.
        monthly_equivalent=(amt_med * _AVG_MONTH_DAYS / Decimal(str(med_gap))).quantize(_CENT),
        regularity=round(regularity, 3),
        active=active,
        next_expected=days[-1] + timedelta(days=round(med_gap)) if active else None,
    )


# Twin-format merge: how far apart two groups' median amounts may be and still
# describe the same relationship (salaries get raises, bills vary).
_MERGE_RATIO = Decimal("1.25")


def _twins(a: Recurring, b: Recurring) -> bool:
    """Could these be one relationship split by a description change?

    Silver converges each row's description to the richest observed, but twin
    formats (Intesa quarterly vs 13-month export) word the same movement
    differently, so the rows one format inserted first can keep a different
    text than their neighbours — one real series, two keys, and the older half
    shows up as a lapsed ghost of the newer. The giveaway is DISJOINT date
    ranges: two genuinely distinct merchants with the same cadence and price
    (two 9.99 subscriptions) overlap in time, a renamed one cannot.
    """
    if (a.amount < 0) != (b.amount < 0) or a.cadence != b.cadence:
        return False
    if not (a.last_date < b.first_date or b.last_date < a.first_date):
        return False
    hi, lo = max(abs(a.amount), abs(b.amount)), min(abs(a.amount), abs(b.amount))
    return lo > 0 and hi / lo <= _MERGE_RATIO


def detect_recurring(rows: list[dict], horizon: date | None = None) -> list[Recurring]:
    """Detect recurring groups in transaction rows.

    ``rows`` need ``value_date``, ``description``, ``amount``, ``account`` and
    ``category`` (internal-transfer legs should already be excluded — a monthly
    top-up to one's own account is a rhythm, not a subscription). ``horizon``
    defaults to the newest ``value_date`` in the data.
    """
    if not rows:
        return []
    horizon = horizon or max(r["value_date"] for r in rows)

    # Group by (merchant key, sign): the same counterparty can both charge and
    # refund, and only one direction is the recurring relationship.
    groups: dict[tuple[str, bool], list[dict]] = {}
    for r in rows:
        key = recurrence_key(r["description"])
        # A numbers-only or near-empty description has no merchant identity to
        # group on; whatever matches it would be coincidence.
        if len(key) < 3:
            continue
        groups.setdefault((key, r["amount"] < 0), []).append(r)

    qualified = [
        (g, members)
        for members in groups.values()
        if (g := _qualify(members, horizon)) is not None
    ]

    # Merge twin-format splits (see _twins). The union must itself keep the
    # beat at the same cadence — the qualification re-run is the safety net
    # that stops two unrelated same-priced series from gluing together.
    merged = True
    while merged:
        merged = False
        for i in range(len(qualified)):
            for j in range(i + 1, len(qualified)):
                if not _twins(qualified[i][0], qualified[j][0]):
                    continue
                union = qualified[i][1] + qualified[j][1]
                g = _qualify(union, horizon)
                if g is not None and g.cadence == qualified[i][0].cadence:
                    qualified[i] = (g, union)
                    del qualified[j]
                    merged = True
                    break
            if merged:
                break

    out = [g for g, _members in qualified]
    # Biggest commitments first; recurring income (positive) sorts before costs
    # of equal size only by accident of sign — the API/UI splits by sign anyway.
    out.sort(key=lambda g: abs(g.monthly_equivalent), reverse=True)
    return out
