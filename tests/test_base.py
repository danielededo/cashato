"""Test unitari dello schema comune e delle utility (nessun DB/dato reale)."""

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
    """Costruisce l'output di ``extract_words()`` da (top, x0, "testo ...")."""
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


class TestAccountHolder:
    """I tre layout reali dell'intestatario (coordinate come nei PDF veri)."""

    def test_revolut_layout(self):
        # blocco a sinistra, CAP su riga propria, colonna destra con altri dati
        words = _words(
            (104.4, 446.2, "Generated on the Jul 18, 2026"),
            (144.0, 39.7, "DANIELE ROSSI"),
            (170.0, 39.7, "Via Roma 1"),
            (170.0, 383.0, "Tax residency: Italy"),
            (182.4, 39.7, "20127"),
            (194.8, 39.7, "Milano"),
        )
        assert addressee_from_words(words) == "DANIELE ROSSI"

    def test_trade_republic_ignores_facing_column_on_the_name_line(self):
        # la riga del nome contiene ANCHE il periodo dell'estratto, a destra:
        # senza il ritaglio per colonna finirebbe dentro al nome
        words = _words(
            (104.7, 73.7, "TRADE REPUBLIC BANK GMBH, BRANCH ITALY 20154 MILANO (MI)"),
            (139.9, 75.2, "DANIELE ROSSI"),
            (139.9, 388.7, "DATA 01 gen 2025 - 17 lug 2026"),
            (148.2, 75.2, "Via Roma 1"),
            (157.2, 75.2, "00100 Roma"),
        )
        assert addressee_from_words(words) == "DANIELE ROSSI"

    def test_intesa_right_column_ignores_left_column(self):
        # "Tipologia conto:" è a sinistra e quasi alla stessa altezza del nome
        words = _words(
            (151.1, 8.0, "Coordinate bancarie: 0140371"),
            (183.1, 283.0, "ROSSI MARIO"),
            (185.0, 8.0, "Tipologia conto:"),
            (193.2, 283.0, "VIA ACQUACORRENTE 3"),
            (197.8, 8.0, "XME Conto"),
            (203.3, 283.0, "00100 ROMA PE"),
        )
        assert addressee_from_words(words) == "ROSSI MARIO"

    def test_none_when_no_address_block(self):
        # export CSV/XLSX: nessun destinatario -> vuoto, non è un errore
        assert addressee_from_words(_words((10.0, 10.0, "DATA OPERAZIONE IMPORTO"))) is None

    def test_street_line_is_not_mistaken_for_a_name(self):
        # niente due righe sopra il CAP che sembrino un nome -> None
        words = _words(
            (100.0, 40.0, "Via Roma 1"),
            (110.0, 40.0, "Scala B interno 4"),
            (120.0, 40.0, "00100 Roma"),
        )
        assert addressee_from_words(words) is None

    def test_given_name_follows_the_source_convention(self):
        assert given_name("DANIELE ROSSI", GIVEN_FIRST) == "Daniele"
        assert given_name("ROSSI MARIO", FAMILY_FIRST) == "Daniele"

    def test_iban_found_next_to_its_label(self):
        # il caso che rompe l'approccio "compatta tutto": senza spazi "IBAN" e
        # "IT47" si attaccano e il confine di parola sparisce
        assert find_iban("IBAN IT47 K030 6915 4601 0000 0014 132") == "IT60X0306912345100000067890"
        assert find_iban("IBAN IT30D0367412345100000011111") == "IT30D0367412345100000011111"
        assert find_iban("Account Number (IT IBAN),IT12A0366912345100000022222") == (
            "IT12A0366912345100000022222"
        )

    def test_iban_absent_or_foreign(self):
        assert find_iban("Account Number N/A") is None
        assert find_iban("LT313250048123456789") is None  # non italiano -> nessun ABI
        assert find_iban("") is None

    def test_abi_is_the_bank_code_inside_the_iban(self):
        assert abi_from_iban("IT60X0306912345100000067890") == "03069"
        assert abi_from_iban("IT71 N036 6901 6007 0617 9872 079") == "03669"
        assert abi_from_iban(None) is None
        assert abi_from_iban("not an iban") is None

    def test_format_holder_titlecases_only_all_caps(self):
        assert format_holder("ROSSI MARIO") == "Rossi Mario"
        # già in maiuscolo/minuscolo: la fonte sa meglio di str.title()
        assert format_holder("Mario de Rossi") == "Mario de Rossi"
