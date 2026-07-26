"""Unit tests for the common schema and utilities (no DB / no real data)."""

from datetime import date
from decimal import Decimal

import pytest

from cashato.parsers.base import (
    FAMILY_FIRST,
    GIVEN_FIRST,
    MoneyParseError,
    Transaction,
    abi_from_iban,
    addressee_from_words,
    assign_occurrence_keys,
    find_iban,
    format_holder,
    given_name,
    normalize_desc,
    parse_money,
)


def _words(*lines):
    """Build the output of ``extract_words()`` from (top, x0, "text ...")."""
    out = []
    for top, x0, text in lines:
        x = x0
        for tok in text.split():
            out.append({"text": tok, "top": top, "x0": x, "x1": x + 6 * len(tok)})
            x += 6 * len(tok) + 4
    return out


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

    def test_minus_after_currency_symbol(self):
        # Review #17: the sign was read BEFORE stripping the currency, so a
        # minus sitting after the symbol was silently dropped (+5.00).
        assert parse_money("€-5.00") == Decimal("-5.00")
        assert parse_money("EUR -5,00", thousands_sep=".", decimal_sep=",") == Decimal("-5.00")
        assert parse_money("€−5.00") == Decimal("-5.00")
        assert parse_money("5.00-") == Decimal("-5.00")

    def test_accounting_parentheses(self):
        assert parse_money("(5.00)") == Decimal("-5.00")
        assert parse_money("(€1,281.64)") == Decimal("-1281.64")
        # The paren must be read AFTER the currency strip, like the minus:
        # "€(5.00)" silently came out positive.
        assert parse_money("€(5.00)") == Decimal("-5.00")
        assert parse_money("EUR (1.234,56)", thousands_sep=".", decimal_sep=",") == Decimal(
            "-1234.56"
        )

    def test_invalid_raises(self):
        with pytest.raises(MoneyParseError):
            parse_money("N/A")

    def test_interior_minus_fails_loud(self):
        # Garbage like "5-0" must error, not silently drop the sign.
        with pytest.raises(MoneyParseError):
            parse_money("5-0")


class TestNormalizeDesc:
    def test_lowercase_accents_punctuation(self):
        assert normalize_desc("Caffè  BAR, Città!") == "caffe bar citta"

    def test_empty(self):
        assert normalize_desc("") == ""


class TestTransaction:
    def test_amount_quantized_to_cents(self):
        # formats with different scale (e.g. 6-decimal CSV) -> 2 decimals
        assert _tx("1000.000000").amount == Decimal("1000.00")

    def test_amount_must_be_decimal(self):
        with pytest.raises(TypeError):
            Transaction(
                value_date=date(2025, 1, 1),
                booking_date=date(2025, 1, 1),
                description="x",
                amount=1000.0,
                currency="EUR",  # float -> error
                account="c",
                source="revolut",
            )

    def test_invalid_source_rejected(self):
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
        # same (account, date, amount, occurrence) -> same key even with a
        # different description (cross-format dedup)
        a = _tx("10.00", descr="Transazione COOP con carta")
        b = _tx("10.00", descr="COOP LOMBARDIA SC")
        assign_occurrence_keys([a])
        assign_occurrence_keys([b])
        assert a.natural_key == b.natural_key

    def test_occurrence_index_distinguishes_identical(self):
        # two identical same-day operations -> distinct keys (#1, #2)
        a = _tx("40.00")
        b = _tx("40.00")
        assign_occurrence_keys([a, b])
        assert a.natural_key != b.natural_key

    def test_same_set_same_keys_across_formats(self):
        # the same set imported twice -> same keys -> dedup
        s1 = [_tx("40.00"), _tx("40.00"), _tx("10.00")]
        s2 = [_tx("40.00", descr="altro testo"), _tx("40.00"), _tx("10.00")]
        assign_occurrence_keys(s1)
        assign_occurrence_keys(s2)
        assert {t.natural_key for t in s1} == {t.natural_key for t in s2}


class TestAccountHolder:
    """The three real account-holder layouts (coordinates as in the real PDFs)."""

    def test_revolut_layout(self):
        # left-hand block, postal code on its own line, right column with other data
        words = _words(
            (104.4, 446.2, "Generated on the Jul 18, 2026"),
            (144.0, 39.7, "MARIO ROSSI"),
            (170.0, 39.7, "Via Roma 1"),
            (170.0, 383.0, "Tax residency: Italy"),
            (182.4, 39.7, "20127"),
            (194.8, 39.7, "Milano"),
        )
        assert addressee_from_words(words) == "MARIO ROSSI"

    def test_trade_republic_ignores_facing_column_on_the_name_line(self):
        # the name line ALSO carries the statement period, on the right: without
        # the per-column crop it would end up inside the name
        words = _words(
            (104.7, 73.7, "TRADE REPUBLIC BANK GMBH, BRANCH ITALY 20154 MILANO (MI)"),
            (139.9, 75.2, "MARIO ROSSI"),
            (139.9, 388.7, "DATA 01 gen 2025 - 17 lug 2026"),
            (148.2, 75.2, "Via Roma 1"),
            (157.2, 75.2, "00100 Roma"),
        )
        assert addressee_from_words(words) == "MARIO ROSSI"

    def test_intesa_right_column_ignores_left_column(self):
        # "Tipologia conto:" sits on the left at almost the same height as the name
        words = _words(
            (151.1, 8.0, "Coordinate bancarie: 0140371"),
            (183.1, 283.0, "ROSSI MARIO"),
            (185.0, 8.0, "Tipologia conto:"),
            (193.2, 283.0, "VIA GARIBALDI 5"),
            (197.8, 8.0, "XME Conto"),
            (203.3, 283.0, "00100 ROMA RM"),
        )
        assert addressee_from_words(words) == "ROSSI MARIO"

    def test_none_when_no_address_block(self):
        # CSV/XLSX exports: no addressee -> empty, and that is not an error
        assert addressee_from_words(_words((10.0, 10.0, "DATA OPERAZIONE IMPORTO"))) is None

    def test_street_line_is_not_mistaken_for_a_name(self):
        # nothing above the postal code that looks like a name -> None
        words = _words(
            (100.0, 40.0, "Via Roma 1"),
            (110.0, 40.0, "Scala B interno 4"),
            (120.0, 40.0, "00100 Roma"),
        )
        assert addressee_from_words(words) is None

    def test_given_name_follows_the_source_convention(self):
        assert given_name("MARIO ROSSI", GIVEN_FIRST) == "Mario"
        assert given_name("ROSSI MARIO", FAMILY_FIRST) == "Mario"

    def test_iban_found_next_to_its_label(self):
        # the case that breaks the "flatten everything" approach: without spaces
        # "IBAN" and "IT47" join up and the word boundary disappears
        assert find_iban("IBAN IT60 X030 6912 3451 0000 0067 890") == "IT60X0306912345100000067890"
        assert find_iban("IBAN IT30D0367412345100000011111") == "IT30D0367412345100000011111"
        assert find_iban("Account Number (IT IBAN),IT12A0366912345100000022222") == (
            "IT12A0366912345100000022222"
        )

    def test_iban_absent_or_foreign(self):
        assert find_iban("Account Number N/A") is None
        assert find_iban("LT313250048123456789") is None  # not Italian -> no ABI
        assert find_iban("") is None

    def test_abi_is_the_bank_code_inside_the_iban(self):
        assert abi_from_iban("IT60X0306912345100000067890") == "03069"
        assert abi_from_iban("IT12 A036 6912 3451 0000 0022 222") == "03669"
        assert abi_from_iban(None) is None
        assert abi_from_iban("not an iban") is None

    def test_format_holder_titlecases_only_all_caps(self):
        assert format_holder("ROSSI MARIO") == "Rossi Mario"
        # already mixed-case: the source knows better than str.title()
        assert format_holder("Mario de Rossi") == "Mario de Rossi"


class TestParserRegressions:
    """Cases that used to lose or corrupt money, from the 2026-07-25 review."""

    def test_crypto_sale_over_one_thousand(self):
        # Revolut amounts use "," as the THOUSANDS separator, so splitting the
        # two legs on a bare comma cut inside the number: a 1,150.00 sale was
        # stored as 1.00 — and with a matching wrong natural_key, so a corrected
        # re-import would not even dedup against it.
        from cashato.parsers.revolut import _crypto_sale_value

        assert _crypto_sale_value("+ €1,150.00, - €1,000.00") == Decimal("1150.00")
        assert _crypto_sale_value("+ €150.00, - €100.00") == Decimal("150.00")
        assert _crypto_sale_value("- €100.00") is None

    def test_movement_rows_are_not_skipped_for_containing_banking_words(self):
        # The skip filter matched anywhere in the row, so a real card-settlement
        # debit was discarded as if it were a header, and a transfer's
        # continuation line was dropped from its description.
        from cashato.parsers.intesa import _SKIP_RE

        assert not _SKIP_RE.search("PAGAMENTO ESTRATTO CONTO CARTA NEXI")
        assert not _SKIP_RE.search("Bonifico a favore di ROSSI MARIO IBAN IT60X0542811101")
        # structural rows still go: they lead with the keyword
        for row in ("Saldo iniziale al 31.12.2023", "Pagina 1 di 13", "Totale accrediti"):
            assert _SKIP_RE.search(row), row
