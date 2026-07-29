"""Merchant/time extraction over every real description shape, per source.

Fixtures are synthetic (fake names, fake cards) but shape-identical to the
statements each parser produces.
"""

from datetime import time

from cashato.parsers.merchant import extract_merchant


def _m(source: str, desc: str) -> tuple[str | None, time | None]:
    r = extract_merchant(source, desc)
    return r.merchant, r.purchase_time


# --- Intesa: POS forms --------------------------------------------------------


def test_intesa_pos_presso_all_caps() -> None:
    desc = (
        "Pagamento POS EFFETTUATO IL 24/01/2024 ALLE ORE 02:27 MEDIANTE LA "
        "CARTA 1234 XXXX XXXX XX99 PRESSO RISTORANTE DA MARIO MILANO"
    )
    assert _m("intesa", desc) == ("RISTORANTE DA MARIO MILANO", time(2, 27))


def test_intesa_pos_presso_merchant_prefixed_and_compact_hour() -> None:
    # 13-month-export twin: merchant repeated up front, hour written "1053".
    desc = (
        "MGP*Esempio Vilnius EFFETTUATO IL 25/08/2025 ALLE ORE 1053 MEDIANTE "
        "LA CARTA 1234 XXXX XXXX XX99 PRESSO MGP*Esempio Vilnius"
    )
    assert _m("intesa", desc) == ("MGP*Esempio Vilnius", time(10, 53))


def test_intesa_pos_presso_title_cased() -> None:
    # The 13-month export Title-Cases the whole text, including keywords.
    desc = (
        "Esempio Srl Alberi Di Vig Effettuato Il 19/02/2026 Alle Ore 0845 "
        "Mediante La Carta 1234 Xxxx Xxxx Xx99 Presso Esempio Srl Alberi Di Vig"
    )
    assert _m("intesa", desc) == ("Esempio Srl Alberi Di Vig", time(8, 45))


def test_intesa_pos_presso_strips_trailing_reference() -> None:
    desc = (
        "Pagamento POS EFFETTUATO IL 02/10/2024 ALLE ORE 10:41 MEDIANTE LA "
        "CARTA 1234 XXXX XXXX XX99 PRESSO PAYPAL *ESEMPIO 35314369001"
    )
    assert _m("intesa", desc) == ("PAYPAL *ESEMPIO", time(10, 41))


def test_intesa_pos_tramite_glued_datetime() -> None:
    desc = (
        "Pagamento Tramite POS COOP LOMBARDIA S.C. VIA MIL20/06-19:10 - "
        "Carta n.1234 XXXX XXXX XX99ABI : 01234 COD.3003594/005415"
    )
    assert _m("intesa", desc) == ("COOP LOMBARDIA S.C. VIA MIL", time(19, 10))


def test_intesa_pos_su_pos_glued_date_time() -> None:
    desc = (
        "Farmacia Esempio 3 Pagamento su POS FARMACIA ESEMPIO 3 18/061832 "
        "Carta n.1234 XXXX XXXX XX99 COD. 3394498/00610"
    )
    assert _m("intesa", desc) == ("FARMACIA ESEMPIO 3", time(18, 32))


def test_intesa_storno_bare_pos_has_nothing() -> None:
    assert _m("intesa", "Storno Pagamento Pos") == (None, None)


# --- Intesa: non-POS forms ----------------------------------------------------


def test_intesa_sdd_direct_debit() -> None:
    desc = (
        "Addebito diretto disposto a favore di A2A S P A "
        "MANDATO PMD000000000A00000000000000001 Cod. Disp. 0126052944086610"
    )
    assert _m("intesa", desc) == ("A2A S P A", None)


def test_intesa_adue_bill() -> None:
    desc = "Pagamento ADUE COD. DISP.:0123081741039786 NOME:COFIDIS S.A. - MANDATO:MNDT000000001P001"
    assert _m("intesa", desc) == ("COFIDIS S.A.", None)


def test_intesa_bancomat_pay_merchant() -> None:
    desc = (
        "Pagamento BANCOMAT PAY presso ESEMPIO SRL data: 16.04 ore: 22:54 "
        "identific.univoco transazione: P2B0000000000000000000"
    )
    assert _m("intesa", desc) == ("ESEMPIO SRL", time(22, 54))


def test_intesa_bancomat_pay_p2p_is_not_a_merchant() -> None:
    desc = (
        "Trasferimento denaro BANCOMAT Pay Da MARIO ROSSI data: 15.12 ore: 21:42 "
        "identific.univoco transazione P2P0000000000000000000"
    )
    assert _m("intesa", desc) == (None, time(21, 42))


def test_intesa_atm_withdrawal_keeps_time_only() -> None:
    # PRESSO names the branch, not a counterparty.
    desc = (
        "Prelievo sportello banca del gruppo CON CARTA N. 1234 XXXX XXXX XX99 "
        "EFFETTUATO IL 23/06/2023 ALLE ORE 19:55 PRESSO ABI 1234 - SPORTELLO "
        "8324 IN VIA ROMA 1 MILANO MI"
    )
    assert _m("intesa", desc) == (None, time(19, 55))


def test_intesa_atm_withdrawal_six_digit_hour() -> None:
    desc = "Prelievo Sportello Banca Del Gruppo Prelievo Cardless presso abi 1234 ATM 8915 EFFETTUATO IL 18/02/2026 ALLE ORE 083900"
    assert _m("intesa", desc) == (None, time(8, 39))


def test_intesa_wire_transfers_yield_nothing() -> None:
    salary = (
        "Stipendio O Pensione COD.DISP. 0126071612960647 SALA SALDO CEDOLINO "
        "GIUGNO 2026 Bonifico a Vostro favore disposto da MITT. ESEMPIO S.P.A. "
        "BENEF. MARIO ROSSI BIC. ORD. XXXXITXXXXX"
    )
    outgoing = (
        "Bonifico istantaneo da voi disposto a favore di MARIO ROSSI "
        "0126070179336835 Bonifico da Voi disposto a favore di MARIO ROSSI Affitto"
    )
    assert _m("intesa", salary) == (None, None)
    assert _m("intesa", outgoing) == (None, None)


# --- Revolut ------------------------------------------------------------------


def test_revolut_description_is_the_merchant() -> None:
    assert _m("revolut", "Booking.com") == ("Booking.com", None)
    assert _m("revolut", "F.lli Esempio Snc") == ("F.lli Esempio Snc", None)


def test_revolut_system_phrases_are_not_merchants() -> None:
    for desc in (
        "Transfer from MARIO ROSSI",
        "Transfer to MARIO ROSSI",
        "To Instant Access Savings",
        "From Instant Access Savings",
        "Crypto sale DOT",
        "Exchanged to EUR",
        "Top-Up by *1234",
        "Apple Pay Top-Up by *1234",
    ):
        assert _m("revolut", desc) == (None, None), desc


# --- Trade Republic -----------------------------------------------------------


def test_tr_card_transaction() -> None:
    assert _m("trade_republic", "COOP LOMBARDIA S.C. - TR Card Transaction") == (
        "COOP LOMBARDIA S.C.",
        None,
    )


def test_tr_card_transaction_collapses_padding() -> None:
    assert _m("trade_republic", "RYANAIR     CPJG2Z0 - TR Card Transaction") == (
        "RYANAIR CPJG2Z0",
        None,
    )


def test_tr_sepa_direct_debit() -> None:
    desc = (
        "BANK GMBH, BRANCH ITALY VIA ESEMPIO 1 TIPO DESCRIZIONE Addebito Sepa "
        "Direct Debit transfer to PayPal Europe S.a.r.l. et Cie S.C.A diretto"
    )
    assert _m("trade_republic", desc) == ("PayPal Europe S.a.r.l. et Cie S.C.A", None)


def test_tr_securities_and_transfers_yield_nothing() -> None:
    for desc in (
        "Core MSCI Europe EUR (Acc) - Savings plan execution IE00B0000000 "
        "iShares III plc, quantity: 0.465820",
        "Bonifico Incoming transfer from MARIO ROSSI",
        "Imposte Stamp Duty Tax (Cash)",
        "Interest payment for payout account",
    ):
        assert _m("trade_republic", desc) == (None, None), desc


# --- guard rails ---------------------------------------------------------------


def test_unknown_source_and_empty_description() -> None:
    assert _m("unknown_bank", "Anything") == (None, None)
    assert _m("intesa", "") == (None, None)


def test_invalid_clock_values_are_dropped() -> None:
    desc = (
        "Pagamento POS EFFETTUATO IL 24/01/2024 ALLE ORE 99:99 MEDIANTE LA "
        "CARTA 1234 XXXX XXXX XX99 PRESSO ESEMPIO MILANO"
    )
    assert _m("intesa", desc) == ("ESEMPIO MILANO", None)
