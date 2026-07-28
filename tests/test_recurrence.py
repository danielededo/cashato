"""Recurring-movement detection on synthetic series.

Each test builds the smallest series that isolates one rule: the cadence
windows, the regularity floor, the amount-spread gate, activity against the
data horizon, and the merchant key. Dates are explicit so the intent is
readable — a detector test that generates its own dates tends to encode the
detector's bugs.
"""

from datetime import date, timedelta
from decimal import Decimal

from cashato.recurrence import detect_recurring, recurrence_key


def _row(d: date, amount: str, desc: str, account: str = "intesa", category: str | None = None):
    return {
        "value_date": d,
        "amount": Decimal(amount),
        "description": desc,
        "account": account,
        "category": category,
    }


def _monthly(day: int, months: int, amount: str, desc: str, year: int = 2026, **kw):
    return [_row(date(year, m, day), amount, desc, **kw) for m in range(1, months + 1)]


HORIZON = date(2026, 6, 30)


def test_monthly_subscription_detected():
    rows = _monthly(15, 6, "-9.99", "NETFLIX.COM", category="subscriptions")
    (g,) = detect_recurring(rows, horizon=HORIZON)
    assert g.cadence == "monthly"
    assert g.active
    assert g.n_occurrences == 6
    assert g.amount == Decimal("-9.99")
    assert g.category == "subscriptions"
    # ~one charge per month, so the monthly equivalent stays close to it
    assert Decimal("-11") < g.monthly_equivalent < Decimal("-9")
    # gaps are [31, 28, 31, 30, 31] days → median 31
    assert g.next_expected == date(2026, 6, 15) + timedelta(days=31)


def test_salary_with_drift_detected_as_income():
    amounts = ["1850.00", "1850.00", "1912.40", "1850.00", "1930.00", "1850.00"]
    rows = [
        _row(date(2026, m, 27), a, "STIPENDIO O PENSIONE GO REPLY SRL")
        for m, a in enumerate(amounts, start=1)
    ]
    (g,) = detect_recurring(rows, horizon=HORIZON)
    assert g.cadence == "monthly"
    assert g.amount > 0


def test_irregular_shopping_not_detected():
    # Same supermarket, no rhythm: gaps 3, 11, 6, 20, 2 days.
    days = [1, 4, 15, 21, 41, 43]
    rows = [_row(date(2026, 3, 1) + timedelta(days=n), "-42.17", "ESSELUNGA MILANO") for n in days]
    assert detect_recurring(rows, horizon=HORIZON) == []


def test_yearly_detected_from_three_occurrences():
    rows = [_row(date(y, 5, 3), "-89.00", "AMAZON PRIME RENEWAL") for y in (2024, 2025, 2026)]
    (g,) = detect_recurring(rows, horizon=HORIZON)
    assert g.cadence == "yearly"
    assert g.active


def test_lapsed_subscription_not_active():
    rows = _monthly(10, 4, "-7.99", "SPOTIFY AB", year=2025)  # ends 2025-04
    (g,) = detect_recurring(rows, horizon=HORIZON)
    assert g.cadence == "monthly"
    assert not g.active
    assert g.next_expected is None


def test_same_day_split_counts_once():
    rows = _monthly(1, 4, "-25.00", "PALESTRA FIT SRL")
    # a second same-day charge: one billing event of 50, not a 15-day cadence
    rows += _monthly(1, 4, "-25.00", "PALESTRA FIT SRL")
    (g,) = detect_recurring(rows, horizon=HORIZON)
    assert g.n_occurrences == 4
    assert g.amount == Decimal("-50.00")


def test_numbers_stripped_from_key():
    assert recurrence_key("NETFLIX.COM 2026-01-15 REF 8842") == recurrence_key(
        "Netflix com 2026-02-15 ref 0011"
    )


def test_variable_bill_needs_regular_dates():
    # Bimonthly bill, amounts vary widely (spread > 0.6) — accepted only
    # because every gap sits in the bimonthly window.
    rows = [
        _row(d, a, "ENEL ENERGIA BOLLETTA")
        for d, a in [
            (date(2025, 9, 16), "-42.00"),
            (date(2025, 11, 15), "-95.00"),
            (date(2026, 1, 16), "-88.00"),
            (date(2026, 3, 16), "-61.00"),
            (date(2026, 5, 15), "-45.00"),
        ]
    ]
    (g,) = detect_recurring(rows, horizon=HORIZON)
    assert g.cadence == "bimonthly"

    # Same amounts on arrhythmic dates: gaps land outside any single window
    # often enough to fail the regularity floor.
    bad = [
        _row(d, a, "MERCATO RIONALE")
        for d, a in [
            (date(2025, 9, 16), "-42.00"),
            (date(2025, 9, 30), "-95.00"),
            (date(2026, 1, 16), "-88.00"),
            (date(2026, 2, 1), "-61.00"),
            (date(2026, 5, 15), "-45.00"),
        ]
    ]
    assert detect_recurring(bad, horizon=HORIZON) == []


def test_refund_does_not_join_the_charge_group():
    rows = _monthly(15, 5, "-9.99", "NETFLIX.COM")
    rows.append(_row(date(2026, 3, 20), "9.99", "NETFLIX.COM"))  # one refund
    (g,) = detect_recurring(rows, horizon=HORIZON)
    assert g.amount < 0
    assert g.n_occurrences == 5


def test_salary_with_raises_and_bonus_detected():
    # A career: 900 → 1400 → 2100 over three years, a thirteenth-month payment
    # each December and one bonus month. Global min-max spread would reject
    # this; consecutive amounts resemble each other, and that is the test.
    rows = []
    for year, base in ((2024, "900.00"), (2025, "1400.00"), (2026, "2100.00")):
        for m in range(1, 13 if year < 2026 else 7):
            rows.append(_row(date(year, m, 16), base, "STIPENDIO ACME SRL"))
        if year < 2026:
            rows.append(_row(date(year, 12, 28), base, "STIPENDIO ACME SRL"))  # tredicesima
    rows.append(_row(date(2026, 2, 20), "3100.00", "STIPENDIO ACME SRL"))  # bonus
    (g,) = detect_recurring(rows, horizon=HORIZON)
    assert g.cadence == "monthly"
    assert g.active
    assert g.amount == Decimal("1400.00")


def test_erratic_amounts_with_loose_dates_rejected():
    # Gaps mostly inside the monthly window but not clockwork (regularity
    # between the two floors), amounts all over the place: no relationship.
    pts = [
        (date(2026, 1, 5), "-12.00"),
        (date(2026, 2, 4), "-190.00"),
        (date(2026, 3, 9), "-45.00"),
        (date(2026, 4, 6), "-310.00"),
        (date(2026, 5, 22), "-19.00"),  # 46-day gap: breaks perfect regularity
        (date(2026, 6, 20), "-77.00"),
    ]
    rows = [_row(d, a, "AMAZON MARKETPLACE") for d, a in pts]
    assert detect_recurring(rows, horizon=HORIZON) == []


def test_twin_format_split_merges_into_one_series():
    # The same salary, worded differently before/after a format boundary
    # (quarterly vs 13-month export): one continuous series, two keys.
    rows = [
        _row(date(2025, m, 27), "-850.00", "BONIFICO SEPA A ROSSI IMMOBILIARE CANONE")
        for m in range(1, 7)
    ] + [
        _row(date(2025, m, 27), "-850.00", "Bonifico disposto Rossi Immobiliare canone locazione")
        for m in range(7, 13)
    ]
    (g,) = detect_recurring(rows, horizon=date(2025, 12, 31))
    assert g.n_occurrences == 12
    assert g.active
    # the richest wording survives, like silver's description convergence
    assert g.description.startswith("Bonifico disposto")


def test_two_distinct_same_priced_subscriptions_do_not_merge():
    # Same cadence, same price, OVERLAPPING in time: two real merchants.
    rows = _monthly(15, 6, "-9.99", "NETFLIX.COM") + _monthly(3, 6, "-9.99", "DISNEY PLUS")
    got = detect_recurring(rows, horizon=HORIZON)
    assert len(got) == 2


def test_empty_and_numeric_descriptions_ignored():
    rows = _monthly(15, 6, "-10.00", "1234 5678")
    assert detect_recurring(rows, horizon=HORIZON) == []
    assert detect_recurring([], horizon=None) == []
