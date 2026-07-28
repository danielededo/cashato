"""File-coverage report: which statement is missing, and how far behind each source is.

Pure functions over already-fetched rows, like ``recurrence.py`` — the
query-api computes this on the fly from gold, so there is no derived table to
go stale and the module unit-tests on synthetic series.

The unit of coverage is the SOURCE, not the account: statements are uploaded
per source, and one Revolut consolidated export carries the cash accounts and
the crypto/savings sections alike. Judged per account, a dormant crypto
pocket looks years "behind" while the export that would cover it is entirely
up to date; judged per source, every account's data testifies for the same
file. Two signals, both read off what the statements left behind:

- **staleness** — how long after the source's last covered day (movement or
  balance anchor, any of its accounts) it has been silent, measured against
  today. The tolerance scales with the source's anchor cadence: a
  quarterly-anchored source is not "behind" until well after the quarter
  closes, a per-day-anchored export is expected back within weeks.
- **holes** — a spacing in the union of the source's covered days much wider
  than its own median rhythm. For a quarterly source that is a missing
  statement; elsewhere it is a window no export covered — or a genuinely
  quiet period, which the data alone cannot tell apart (no movements means
  no anchors either). Holes are hints to check, never verdicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import median

# A spacing this many times the source's median day-to-day rhythm is a hole.
HOLE_FACTOR = 1.8
# ...but never flag spacings shorter than this: dense sources have a natural
# rhythm of quiet weeks that means nothing.
MIN_HOLE_DAYS = 45

# Staleness grace: how long past its own anchor cadence a source may be
# silent before it counts as behind. Dense-anchored sources get the floor.
STALE_FACTOR = 1.5
MIN_STALE_DAYS = 45


@dataclass
class Hole:
    """A window between two covered days that no data touches."""

    from_date: date
    to_date: date
    days: int


@dataclass
class SourceCoverage:
    source: str
    accounts: list[str]
    n_movements: int
    n_anchors: int
    covered_from: date | None
    #: The last day any data of this source covers.
    covered_until: date | None
    #: Median days between anchors; None below 3 anchors (no rhythm to speak of).
    anchor_cadence_days: int | None
    stale_days: int
    #: Behind schedule given its own cadence — time to upload a newer statement.
    stale: bool
    holes: list[Hole]


def _spacing_median(days: list[date]) -> float | None:
    if len(days) < 3:
        return None
    return float(median((b - a).days for a, b in zip(days, days[1:], strict=False)))


def source_coverage(
    source: str,
    accounts: dict[str, tuple[list[date], list[date]]],
    today: date,
) -> SourceCoverage:
    """Coverage of one source. ``accounts`` maps account id to its
    (movement dates, anchor dates); the union of all of them is what counts."""
    anchors = sorted({d for movs, ancs in accounts.values() for d in ancs})
    covered = sorted({d for movs, ancs in accounts.values() for d in movs} | set(anchors))

    cadence = _spacing_median(anchors)
    covered_until = covered[-1] if covered else None
    stale_days = (today - covered_until).days if covered_until else 0
    stale_after = max(MIN_STALE_DAYS, STALE_FACTOR * cadence) if cadence else MIN_STALE_DAYS
    # No data at all is not "stale", it is empty; the UI states that itself.
    stale = covered_until is not None and stale_days > stale_after

    # Holes in the union of every covered day: a movement or an anchor from
    # ANY of the source's accounts covers the file for that day.
    rhythm = _spacing_median(covered)
    threshold = max(MIN_HOLE_DAYS, HOLE_FACTOR * rhythm) if rhythm else MIN_HOLE_DAYS
    holes = [
        Hole(from_date=a, to_date=b, days=(b - a).days)
        for a, b in zip(covered, covered[1:], strict=False)
        if (b - a).days > threshold
    ]

    return SourceCoverage(
        source=source,
        accounts=sorted(accounts),
        n_movements=sum(len(movs) for movs, _ancs in accounts.values()),
        n_anchors=len(anchors),
        covered_from=covered[0] if covered else None,
        covered_until=covered_until,
        anchor_cadence_days=round(cadence) if cadence else None,
        stale_days=stale_days,
        stale=stale,
        holes=holes,
    )


def coverage_report(
    movements: list[dict], anchors: list[dict], today: date
) -> list[SourceCoverage]:
    """Build per-source coverage from transaction and anchor rows.

    ``movements`` need ``account``, ``source``, ``value_date``; ``anchors``
    need ``account``, ``source``, ``balance_date``. Sorted worst-first
    (stale, then widest hole), so the UI's top row is the one that needs a
    statement uploaded.
    """
    by_source: dict[str, dict[str, tuple[list[date], list[date]]]] = {}

    def slot(source: str, account: str) -> tuple[list[date], list[date]]:
        return by_source.setdefault(source, {}).setdefault(account, ([], []))

    for m in movements:
        slot(m["source"], m["account"])[0].append(m["value_date"])
    for a in anchors:
        slot(a["source"], a["account"])[1].append(a["balance_date"])

    out = [source_coverage(src, accounts, today) for src, accounts in sorted(by_source.items())]
    out.sort(key=lambda c: (not c.stale, -max((h.days for h in c.holes), default=0), c.source))
    return out
