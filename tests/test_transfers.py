"""Unit tests for internal-transfer detection (no DB)."""

from datetime import date
from decimal import Decimal

from libs.transfers import Leg, find_pairs


def _leg(id, acc, amount, d=(2025, 1, 10), desc="x"):
    return Leg(id, f"nk{id}", acc, date(*d), Decimal(str(amount)), desc)


def test_same_day_opposite_amount_different_account_pairs():
    legs = [_leg(1, "intesa", "-1000.00"), _leg(2, "trade_republic", "1000.00")]
    pairs = find_pairs(legs)
    assert len(pairs) == 1 and {pairs[0][0], pairs[0][1]} == {1, 2}


def test_same_account_not_paired():
    legs = [_leg(1, "intesa", "-50.00"), _leg(2, "intesa", "50.00")]
    assert find_pairs(legs) == []


def test_beyond_window_not_paired():
    legs = [
        _leg(1, "intesa", "-50.00", (2025, 1, 1)),
        _leg(2, "revolut_eur", "50.00", (2025, 1, 20)),
    ]
    assert find_pairs(legs, window_days=3) == []


def test_guard_requires_hint_when_not_same_day():
    # different day, no transfer hint -> not paired
    legs = [
        _leg(1, "intesa", "-20.00", (2025, 1, 10), "coop"),
        _leg(2, "revolut_eur", "20.00", (2025, 1, 12), "rimborso"),
    ]
    assert find_pairs(legs) == []
    # same amounts but with a transfer hint -> paired
    legs2 = [
        _leg(1, "intesa", "-20.00", (2025, 1, 10), "bonifico a vostro favore"),
        _leg(2, "revolut_eur", "20.00", (2025, 1, 12), "transfer from me"),
    ]
    assert len(find_pairs(legs2)) == 1


def test_one_to_one_assignment():
    # one outflow, two candidate inflows same day -> only one pair
    legs = [
        _leg(1, "intesa", "-100.00"),
        _leg(2, "revolut_eur", "100.00"),
        _leg(3, "trade_republic", "100.00"),
    ]
    assert len(find_pairs(legs)) == 1


def test_group_id_deterministic():
    legs = [_leg(1, "intesa", "-1000.00"), _leg(2, "trade_republic", "1000.00")]
    assert find_pairs(legs)[0][2] == find_pairs(legs)[0][2]
