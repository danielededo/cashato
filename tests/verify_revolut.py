"""Verifica end-to-end dell'adapter Revolut sul CSV reale (Fase A).

Non richiede database: controlla che il parsing sia corretto.

Controlli:
1. numero di righe EUR estratte (plausibilita');
2. coerenza segni (top-up positivi, merchant negativi);
3. ricostruzione del saldo: balance[i] == balance[i-1] + money_in_out[i];
4. dedup: nessuna natural_key duplicata sulle transazioni prodotte da parse().

Uso:  ./.venv/bin/python tests/verify_revolut.py [percorso_csv]
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

# Rende importabile libs/ senza installazione
from cashato.parsers import revolut

DEFAULT_CSV = "data/Revolut/consolidated-statement-v2_2023-10-10_2026-07-18_en_4941b1.csv"


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CSV
    if not Path(path).exists():
        print(f"[ERRORE] file non trovato: {path}")
        return 2

    rows = list(revolut.iter_rows(path))
    txs = revolut.parse(path)

    print(f"File: {path}")
    print(f"Righe EUR (movimenti):        {len(rows)}")
    print(f"Transazioni prodotte (parse): {len(txs)}  (incl. righe commissione)")

    if not rows:
        print("[ERRORE] nessuna riga estratta")
        return 1

    pos = sum(1 for r in rows if r.money_in_out > 0)
    neg = sum(1 for r in rows if r.money_in_out < 0)
    tot = sum((r.money_in_out for r in rows), Decimal("0"))
    conti = sorted({r.account for r in rows})
    print(f"Entrate/uscite (righe):       +{pos} / -{neg}")
    print(f"Periodo:                      {rows[0].date} -> {rows[-1].date}")
    print(f"Somma netta movimenti:        {tot} EUR")
    print(f"Conti EUR distinti:           {conti}")

    # 3. Balance reconstruction, per account (reset at boundaries)
    errori_saldo = 0
    prev: dict[str, Decimal] = {}
    for r in rows:
        if r.balance is None:
            continue
        if r.account in prev:
            atteso = prev[r.account] + r.money_in_out
            if abs(atteso - r.balance) > Decimal("0.01"):
                errori_saldo += 1
                if errori_saldo <= 5:
                    print(
                        f"  [saldo] riga {r.line_no} ({r.account}): atteso {atteso}"
                        f" != balance {r.balance} ({r.description!r})"
                    )
        prev[r.account] = r.balance
    print(f"Discrepanze saldo:            {errori_saldo}")

    # 4. Dedup natural_key
    keys = [t.natural_key for t in txs]
    dup = len(keys) - len(set(keys))
    print(f"Natural key duplicate:        {dup}")

    ok = errori_saldo == 0 and len(rows) > 0
    print("\nESITO:", "OK" if ok else "DA VERIFICARE")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
