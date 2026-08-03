"""Merchant + time-of-day extraction from raw transaction descriptions.

Statements bury the counterparty inside boilerplate ("Pagamento POS EFFETTUATO
IL ... PRESSO <merchant>", "<merchant> - TR Card Transaction"); this module
digs it out so gold can answer "where does the money go" by merchant, not by
raw text. Extraction is per-source (each bank has its own boilerplate) and
deliberately conservative: a form we do not recognize yields None, never a
guess — a wrong merchant pollutes every aggregate built on top.

The extracted fields are DERIVED data: they are recomputed whenever the
description converges to a richer text (same gate as the category), so they
never need manual repair. Bank transfers and P2P payments yield no merchant by
design — a person is not a merchant, and the spend-by-merchant view must not
leak counterparty names into what reads as a shopping report; recurrence
detection already covers salary/rent counterparties.

Time-of-day is extracted even when the merchant is not (e.g. ATM withdrawals):
the hour pattern is analytics-worthy on its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import time

__all__ = ["MerchantInfo", "extract_merchant"]


@dataclass(frozen=True)
class MerchantInfo:
    merchant: str | None
    purchase_time: time | None


_NONE = MerchantInfo(None, None)

# --- shared helpers -----------------------------------------------------------


def _clock(hh: str, mm: str) -> time | None:
    h, m = int(hh), int(mm)
    if h > 23 or m > 59:
        return None
    return time(h, m)


def _tidy(name: str) -> str | None:
    """Collapse whitespace and drop trailing long numeric references
    ("PAYPAL *SPOTIFY 35314369001" -> "PAYPAL *SPOTIFY")."""
    # Trailing "." stays: it closes abbreviations ("S.C.", "S.A."), not noise.
    name = re.sub(r"\s+", " ", name).strip(" -,:")
    name = re.sub(r"(?:\s+\d{5,})+$", "", name).strip(" -,:")
    return name or None


# --- Intesa Sanpaolo ----------------------------------------------------------
#
# The quarterly statement writes operations in ALL CAPS, the 13-month export
# Title-Cases the same text ("PRESSO X" vs "Presso X"), so every pattern here
# is case-insensitive and the merchant keeps the casing of the surviving
# description; consumers group case-insensitively.

# "EFFETTUATO IL 24/01/2024 ALLE ORE 02:27" / "ALLE ORE 1053" / "ALLE ORE 083900"
_I_ALLE_ORE = re.compile(r"ALLE ORE\s+(\d{2})[:.]?(\d{2})(?:\d{2})?\b", re.IGNORECASE)
# BANCOMAT Pay: "data: 15.12 ore: 21:42"
_I_ORE = re.compile(r"\bore:?\s*(\d{2})[:.](\d{2})\b", re.IGNORECASE)
# "Pagamento Tramite POS <merchant+address>20/06-19:10 - Carta ..." (glued date)
_I_TRAMITE = re.compile(
    r"Pagamento Tramite POS\s+(.+?)\s*(\d{2})/(\d{2})-(\d{2})[:.](\d{2})", re.IGNORECASE
)
# "Pagamento su POS <merchant> 18/061832 Carta ..." (glued date+time)
_I_SU_POS = re.compile(
    r"Pagamento su POS\s+(.+?)\s*(\d{2})/(\d{2})(\d{2})(\d{2})\b", re.IGNORECASE
)
# card POS payments: "... MEDIANTE LA CARTA ... PRESSO <merchant [city]>"
_I_PRESSO = re.compile(r"\bPRESSO\s+(.+)$", re.IGNORECASE)
# "Pagamento BANCOMAT PAY presso ENGAGIGO SRL data: 16.04 ore: 22:54 ..."
_I_BPAY_PRESSO = re.compile(r"BANCOMAT PAY presso\s+(.+?)\s+data:", re.IGNORECASE)
# "Addebito diretto disposto a favore di A2A S P A MANDATO ..." (SDD: utilities)
_I_SDD = re.compile(
    r"Addebito diretto disposto a favore di\s+(.+?)(?:\s+MANDATO\b|\s+Cod\.?\s|\s*$)",
    re.IGNORECASE,
)
# "Pagamento ADUE COD. DISP.:... NOME:COFIDIS S.A. - MANDATO:..." (CBILL/ADUE bills)
_I_ADUE = re.compile(r"Pagamento ADUE\b.*?NOME:\s*(.+?)\s*(?:-\s*MANDATO\b|MANDATO\b|$)", re.IGNORECASE)

# Operations that carry PRESSO/ORE but no merchant: cash at a branch/ATM is a
# place, not a counterparty; wire transfers and P2P name people.
_I_NO_MERCHANT = re.compile(
    r"^\s*(?:Prelievo|Versamento|Bonifico|Stipendio|Trasferimento denaro)", re.IGNORECASE
)


def _intesa(desc: str) -> MerchantInfo:
    when: time | None = None
    m = _I_ALLE_ORE.search(desc) or _I_ORE.search(desc)
    if m:
        when = _clock(m.group(1), m.group(2))

    if _I_NO_MERCHANT.search(desc):
        return MerchantInfo(None, when)

    if m := _I_TRAMITE.search(desc):
        return MerchantInfo(_tidy(m.group(1)), when or _clock(m.group(4), m.group(5)))
    if m := _I_SU_POS.search(desc):
        return MerchantInfo(_tidy(m.group(1)), when or _clock(m.group(4), m.group(5)))
    if m := _I_BPAY_PRESSO.search(desc):
        return MerchantInfo(_tidy(m.group(1)), when)
    if m := _I_PRESSO.search(desc):
        return MerchantInfo(_tidy(m.group(1)), when)
    if m := _I_SDD.search(desc):
        return MerchantInfo(_tidy(m.group(1)), when)
    if m := _I_ADUE.search(desc):
        return MerchantInfo(_tidy(m.group(1)), when)
    return MerchantInfo(None, when)


# --- Revolut ------------------------------------------------------------------
#
# The consolidated statement's description IS the merchant for card spending
# ("Booking.com", "Panisimo"); everything that is not a purchase starts with a
# small set of system phrases.

_R_SYSTEM = re.compile(
    r"^\s*(?:Transfer (?:from|to)\b|To\b|From\b|Crypto\b|Exchange[d]?\b|Top[- ]?up\b|"
    r"Savings\b|Payment from\b|Balance migration\b)",
    re.IGNORECASE,
)
# "Apple Pay Top-Up by *1234" — a top-up wearing a wallet brand, not a purchase.
_R_TOPUP = re.compile(r"\bTop[- ]?Up by \*", re.IGNORECASE)


def _revolut(desc: str) -> MerchantInfo:
    if _R_SYSTEM.search(desc) or _R_TOPUP.search(desc):
        return _NONE
    return MerchantInfo(_tidy(desc), None)


# --- Trade Republic -----------------------------------------------------------

# "COOP LOMBARDIA S.C. - TR Card Transaction"
_T_CARD = re.compile(r"^(.*?)\s*-\s*TR Card Transaction", re.IGNORECASE)
# "... Sepa Direct Debit transfer to PayPal Europe S.a.r.l. et Cie S.C.A diretto"
# (the trailing "diretto" is the tail of the bilingual gloss, not the name)
_T_SDD = re.compile(r"Sepa Direct Debit transfer to\s+(.+?)(?:\s+diretto\s*)?$", re.IGNORECASE)


def _trade_republic(desc: str) -> MerchantInfo:
    if m := _T_CARD.search(desc):
        return MerchantInfo(_tidy(m.group(1)), None)
    if m := _T_SDD.search(desc):
        return MerchantInfo(_tidy(m.group(1)), None)
    return _NONE


# One strategy PER REGISTERED SOURCE, and the test suite enforces the
# coverage: a new adapter must add its entry here (use ``None`` to declare
# "this source's texts carry no extractable merchant"). Without that guard a
# drop-in source silently got no merchant, no purchase_time and no error.
_BY_SOURCE = {
    "intesa": _intesa,
    "revolut": _revolut,
    "trade_republic": _trade_republic,
}


def extract_merchant(source: str, description: str | None) -> MerchantInfo:
    """Best-effort (merchant, purchase_time) for one transaction.

    Unknown sources and unrecognized forms return (None, None): the fields are
    an enrichment, never a requirement.
    """
    if not description:
        return _NONE
    fn = _BY_SOURCE.get(source)
    if fn is None:
        return _NONE
    return fn(description)
