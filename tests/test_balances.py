"""Balance anchors + reconciliation, exercised against the demo dataset.

The demo files are generated with coherent running balances, so the strongest
test possible is the real thing: extract every anchor and every transaction
from every file, dedup across formats exactly like silver does, and require
each inter-anchor interval to reconcile to the cent. A parser that drops or
invents a row breaks this — that is the point.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from cashato.parsers import intesa, registry, revolut
from cashato.parsers.trade_republic import _amounts_in_row, _Cols

DEMO = Path(__file__).parent.parent / "demo"

_FILES = {
    "revolut": ["revolut_consolidated_statement.csv", "revolut_consolidated_statement.pdf"],
    "trade_republic": ["trade_republic_rendiconto.pdf", "trade_republic_transactions.csv"],
    "intesa": sorted(p.name for p in DEMO.glob("intesa_estratto_conto_*.pdf"))
    + ["intesa_lista_operazioni_13m.xlsx"],
}


def _all():
    """(unique transactions by account, anchors by (account, date)) across files."""
    by_key: dict[str, object] = {}
    anchors: dict[tuple[str, date], Decimal] = {}
    basis: dict[str, str] = {}  # per account — uniform per source
    for source, names in _FILES.items():
        for name in names:
            path = DEMO / name
            for t in registry.ADAPTERS[source](path):
                by_key.setdefault(t.natural_key, t)
            extract = registry.BALANCE_EXTRACTORS.get(source)
            if extract:
                for a in extract(path):
                    anchors[(a.account, a.balance_date)] = a.balance
                    basis[a.account] = a.basis
    tx = defaultdict(list)
    for t in by_key.values():
        tx[t.account].append(t)
    return tx, anchors, basis


def test_every_interval_reconciles():
    tx, anchors, basis = _all()
    per_account = defaultdict(list)
    for (acct, d), bal in anchors.items():
        per_account[acct].append((d, bal))
    assert per_account, "no anchors extracted at all"
    intervals = 0
    for acct, ans in per_account.items():
        ans.sort()
        # Sum by the anchors' declared basis date, exactly like the gold view.
        key = (lambda t: t.booking_date) if basis[acct] == "booking" else (lambda t: t.value_date)
        for (d1, b1), (d2, b2) in zip(ans, ans[1:], strict=False):
            got = sum((t.amount for t in tx[acct] if d1 < key(t) <= d2), Decimal(0))
            assert got == b2 - b1, (
                f"{acct} {d1}->{d2}: parsed {got}, statement says {b2 - b1}"
            )
            intervals += 1
    assert intervals > 200  # revolut per-day + TR per-day + intesa quarters


def test_revolut_anchors_identical_across_formats():
    csv_a = {(a.account, a.balance_date): a.balance
             for a in revolut.extract_balances(DEMO / "revolut_consolidated_statement.csv")}
    pdf_a = {(a.account, a.balance_date): a.balance
             for a in revolut.extract_balances(DEMO / "revolut_consolidated_statement.pdf")}
    assert csv_a and csv_a == pdf_a


def test_intesa_quarterly_anchors():
    xs = intesa.extract_balances(DEMO / "intesa_estratto_conto_2025_Q2.pdf")
    a = {x.balance_date: x.balance for x in xs}
    # "Saldo iniziale al" names the last day of the PREVIOUS quarter.
    assert set(a) == {date(2025, 3, 31), date(2025, 6, 30)}
    # The statement orders and totals by booking date; the anchors say so.
    assert all(x.basis == "booking" for x in xs)
    # The 13-month export carries no balances: absent, not an error.
    assert intesa.extract_balances(DEMO / "intesa_lista_operazioni_13m.xlsx") == []


def test_revolut_fee_stays_inside_the_amount():
    """The Fees column is informational (the balance chain proves the fee is
    already inside Money in/out): no separate fee transaction may be emitted."""
    rows = registry.ADAPTERS["revolut"](DEMO / "revolut_consolidated_statement.csv")
    fee_rows = [t for t in rows if t.description.startswith("Fee:")]
    assert fee_rows == []


def _w(x0: float, text: str, x1: float | None = None) -> dict:
    return {"text": text, "x0": x0, "x1": x1 if x1 is not None else x0 + 6 * len(text)}


def test_tr_balance_keeps_its_sign():
    cols = _Cols(inflow=400.0, outflow=470.0, balance=540.0)
    # glued minus
    amount, kind, balance = _amounts_in_row(
        [_w(380, "57,08", 400), _w(510, "-31,89", 540)], cols
    )
    assert (amount, kind, balance) == (Decimal("57.08"), "inflow", Decimal("-31.89"))
    # minus as its own token, adjacent to the number
    amount, kind, balance = _amounts_in_row(
        [_w(440, "57,08", 466), _w(506, "-", 509), _w(511, "31,89", 540)], cols
    )
    assert (amount, kind, balance) == (Decimal("57.08"), "outflow", Decimal("-31.89"))
    # a positive balance stays positive
    *_, balance = _amounts_in_row([_w(510, "31,89", 540)], cols)
    assert balance == Decimal("31.89")
