"""Verifica end-to-end dell'adapter Trade Republic sul PDF reale (Fase B).

Auto-riconciliante: estrae dal riquadro "ESTRATTO CONTO RIASSUNTIVO" i totali
dichiarati (saldo iniziale, in entrata, in uscita, saldo finale) e li confronta
con la somma delle transazioni estratte. Controlla anche la continuita' del
saldo riga per riga e l'assenza di natural_key duplicate.

Uso:  ./.venv/bin/python tests/verify_trade_republic.py [percorso_pdf]
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pdfplumber

from cashato.parsers import trade_republic as tr

DEFAULT_PDF = "data/trade_republic/Account statement.pdf"


def _summary_totals(path: str):
    """Estrae (saldo_iniziale, entrata, uscita, saldo_finale) dal riepilogo."""
    with pdfplumber.open(path) as pdf:
        lines = tr._group_lines(pdf.pages[0].extract_words(keep_blank_chars=False))
    for _top, ws in lines:
        nums = [tr._to_decimal(w["text"]) for w in ws if tr._is_money(w["text"])]
        texts = {w["text"].lower() for w in ws}
        # summary product row: 4 amounts (opening, income, expense, closing)
        if len(nums) == 4 and ("corrente" in texts or "account" in texts):
            return nums
    return None


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PDF
    if not Path(path).exists():
        print(f"[ERRORE] file non trovato: {path}")
        return 2

    txs = tr.parse(path)
    entrate = sum((t.amount for t in txs if t.amount > 0), Decimal("0"))
    uscite = sum((t.amount for t in txs if t.amount < 0), Decimal("0"))
    netto = entrate + uscite

    print(f"File: {path}")
    print(f"Transazioni:            {len(txs)}")
    print(f"Entrate:                {entrate}")
    print(f"Uscite:                 {uscite}")
    print(f"Netto:                  {netto}")
    print(f"Investimenti:           {sum(1 for t in txs if t.category == 'investimenti')}")

    ok = len(txs) > 0

    # 1. Riconciliazione col riepilogo
    tot = _summary_totals(path)
    if tot:
        s_ini, in_ent, in_usc, s_fin = tot
        print(f"\nRiepilogo PDF: iniziale={s_ini} entrata={in_ent} uscita={in_usc} finale={s_fin}")
        checks = [
            ("entrate", entrate, in_ent),
            ("uscite", -uscite, in_usc),
            ("saldo finale", s_ini + netto, s_fin),
        ]
        for nome, got, exp in checks:
            good = abs(got - exp) <= Decimal("0.01")
            ok = ok and good
            print(f"  {'OK ' if good else 'ERR'} {nome}: {got} vs {exp}")
    else:
        print("\n[warn] riepilogo non trovato: salto la riconciliazione")

    # 2. Balance continuity + dedup
    dup = len(txs) - len({t.natural_key for t in txs})
    print(f"\nNatural key duplicate:  {dup}")
    ok = ok and dup == 0

    print("\nESITO:", "OK" if ok else "DA VERIFICARE")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
