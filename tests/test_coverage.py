"""Coverage report on synthetic series: staleness scaled to cadence, holes in
the union of a source's covered days, dormant sections excused by fresh ones."""

from datetime import date, timedelta

from cashato.coverage import coverage_report, source_coverage

TODAY = date(2026, 7, 28)


def _quarters(start: date, n: int) -> list[date]:
    """n quarter-end dates starting at start (itself a quarter end)."""
    out, d = [], start
    for _ in range(n):
        out.append(d)
        nxt = (d + timedelta(days=45)).replace(day=1)  # into the next quarter
        m = ((nxt.month - 1) // 3 + 1) * 3
        d = (date(nxt.year, m, 1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return out


def test_quarterly_source_in_good_standing():
    # Anchors through 2026-06-30; late July is well within a quarter's grace.
    anchors = _quarters(date(2024, 12, 31), 7)  # ... 2026-06-30
    movs = [date(2025, 1, 10) + timedelta(days=3 * i) for i in range(180)]  # dense daily-ish
    c = source_coverage("intesa", {"intesa": (movs, anchors)}, TODAY)
    assert not c.stale
    assert c.holes == []
    assert c.anchor_cadence_days and 85 <= c.anchor_cadence_days <= 95


def test_missing_middle_statement_leaves_a_hole():
    # Movements exist only where statements covered them: a lost quarter has
    # neither movements nor its anchor.
    anchors = _quarters(date(2024, 12, 31), 7)
    lost_q = anchors.pop(3)
    movs = []
    for i in range(0, 540, 2):
        d = date(2025, 1, 2) + timedelta(days=i)
        if abs((d - lost_q).days) > 46:
            movs.append(d)
    c = source_coverage("intesa", {"intesa": (movs, anchors)}, TODAY)
    assert any(h.from_date < lost_q < h.to_date for h in c.holes)


def test_quarterly_source_behind_schedule():
    anchors = _quarters(date(2024, 3, 31), 7)  # ends 2025-09-30
    c = source_coverage("intesa", {"intesa": ([], anchors)}, TODAY)
    assert c.stale  # two full quarters have closed since


def test_dormant_section_excused_by_a_fresh_sibling():
    # One consolidated export: crypto pocket quiet since 2024, cash fresh.
    cash = [date(2026, 6, 1) + timedelta(days=i) for i in range(45)]
    crypto = [date(2024, 1, 2)]
    c = source_coverage(
        "revolut",
        {"revolut_personal_eur": (cash, cash), "revolut_crypto": (crypto, [])},
        TODAY,
    )
    assert not c.stale
    assert c.accounts == ["revolut_crypto", "revolut_personal_eur"]
    # the crypto→cash silence is ancient history only if some account covers
    # it; here nothing does, so it surfaces as a (single, honest) hole
    assert len(c.holes) == 1


def test_one_account_quiet_while_the_other_covers():
    # The personal card goes quiet for two months; the joint account keeps
    # moving. The FILE covers the window, so no hole.
    a = [date(2026, 1, 1) + timedelta(days=i) for i in range(30)] + [
        date(2026, 4, 1) + timedelta(days=i) for i in range(30)
    ]
    b = [date(2026, 1, 15) + timedelta(days=7 * i) for i in range(20)]
    c = source_coverage("revolut", {"personal": (a, []), "joint": (b, [])}, TODAY)
    assert c.holes == []


def test_dense_source_stale_after_the_floor():
    anchors = [date(2026, 5, 1) + timedelta(days=i) for i in range(30)]  # ends 2026-05-30
    c = source_coverage("revolut", {"r": ([], anchors)}, TODAY)
    assert c.stale  # 59 days > the 45-day floor
    fresh = [date(2026, 6, 20) + timedelta(days=i) for i in range(30)]
    assert not source_coverage("revolut", {"r": ([], fresh)}, TODAY).stale


def test_empty_source_is_not_stale():
    c = source_coverage("x", {"x": ([], [])}, TODAY)
    assert not c.stale
    assert c.covered_until is None


def test_report_groups_by_source_and_sorts_worst_first():
    rows = [
        {"account": "a1", "source": "fresh", "value_date": TODAY - timedelta(days=3)},
        {"account": "b1", "source": "old", "value_date": TODAY - timedelta(days=400)},
    ]
    anchors = [{"account": "a1", "source": "fresh", "balance_date": TODAY - timedelta(days=3)}]
    got = coverage_report(rows, anchors, TODAY)
    assert [c.source for c in got] == ["old", "fresh"]
    assert got[0].stale and not got[1].stale
