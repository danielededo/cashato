"""Test unitari dello schema comune e delle utility (nessun DB/dato reale)."""

from datetime import date
from decimal import Decimal

import pytest

from libs.parsers.base import (
    MoneyParseError,
    Transaction,
    assign_occurrence_keys,
    normalize_desc,
    parse_money,
)


def _tx(amount, descr="x", account="revolut_eur", d=date(2025, 1, 1)):
    return Transaction(
        value_date=d,
        booking_date=d,
        description=descr,
        amount=Decimal(str(amount)),
        currency="EUR",
        account=account,
        source="revolut",
    )


class TestParseMoney:
    def test_revolut_us_format(self):
        assert parse_money("€6.99") == Decimal("6.99")
        assert parse_money("-€6.99") == Decimal("-6.99")
        assert parse_money("€1,281.64") == Decimal("1281.64")

    def test_intesa_it_format(self):
        assert parse_money("1.234,56", thousands_sep=".", decimal_sep=",") == Decimal("1234.56")
        assert parse_money("110,92", thousands_sep=".", decimal_sep=",") == Decimal("110.92")
        assert parse_money("-35145,61", thousands_sep=".", decimal_sep=",") == Decimal("-35145.61")

    def test_unicode_minus(self):
        assert parse_money("−5,00", thousands_sep=".", decimal_sep=",") == Decimal("-5.00")

    def test_invalid_raises(self):
        with pytest.raises(MoneyParseError):
            parse_money("N/A")


class TestNormalizeDesc:
    def test_lowercase_accents_punctuation(self):
        assert normalize_desc("Caffè  BAR, Città!") == "caffe bar citta"

    def test_empty(self):
        assert normalize_desc("") == ""


class TestTransaction:
    def test_importo_quantized_to_cents(self):
        # formati con scale diverse (es. CSV a 6 decimali) -> 2 decimali
        assert _tx("1000.000000").amount == Decimal("1000.00")

    def test_importo_must_be_decimal(self):
        with pytest.raises(TypeError):
            Transaction(
                value_date=date(2025, 1, 1),
                booking_date=date(2025, 1, 1),
                description="x",
                amount=1000.0,
                currency="EUR",  # float -> errore
                account="c",
                source="revolut",
            )

    def test_invalid_tipo_origine(self):
        with pytest.raises(ValueError):
            _tx_bad = Transaction(
                value_date=date(2025, 1, 1),
                booking_date=date(2025, 1, 1),
                description="x",
                amount=Decimal("1"),
                currency="EUR",
                account="c",
                source="sconosciuta",
            )


class TestDedup:
    def test_natural_key_ignores_description(self):
        # stessa (account, data, amount, occorrenza) -> stessa chiave anche con
        # description diversa (dedup cross-formato)
        a = _tx("10.00", descr="Transazione COOP con carta")
        b = _tx("10.00", descr="COOP LOMBARDIA SC")
        assign_occurrence_keys([a])
        assign_occurrence_keys([b])
        assert a.natural_key == b.natural_key

    def test_occurrence_index_distinguishes_identical(self):
        # due operazioni identiche stesso giorno -> chiavi diverse (#1, #2)
        a = _tx("40.00")
        b = _tx("40.00")
        assign_occurrence_keys([a, b])
        assert a.natural_key != b.natural_key

    def test_same_set_same_keys_across_formats(self):
        # lo stesso insieme importato due volte -> stesse chiavi -> dedup
        s1 = [_tx("40.00"), _tx("40.00"), _tx("10.00")]
        s2 = [_tx("40.00", descr="altro testo"), _tx("40.00"), _tx("10.00")]
        assign_occurrence_keys(s1)
        assign_occurrence_keys(s2)
        assert {t.natural_key for t in s1} == {t.natural_key for t in s2}
