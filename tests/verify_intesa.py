"""Verifica end-to-end dell'adapter Intesa sui 21 estratti trimestrali (Fase B).

Per ogni file riconcilia le somme estratte con i totali dichiarati a pagina 1
(Totale accrediti / Totale addebiti / Saldo iniziale+finale). Poi deduplica
l'insieme completo via natural_key per misurare le sovrapposizioni ai bordi
trimestre.

Uso:  ./.venv/bin/python tests/verify_intesa.py [cartella]
"""

from __future__ import annotations

import glob
import sys
from decimal import Decimal
from pathlib import Path

import pdfplumber

from cashato.parsers import intesa
from cashato.parsers.base import parse_money

DEFAULT_DIR = "data/intesa"
PATTERN = "*Estratto conto trimestrale*.pdf"

CENT = Decimal("0.01")


def _summary(path: str) -> dict:
    """Extract the totals from the page-1 summary (fragmented amounts)."""
    with pdfplumber.open(path) as pdf:
        lines = intesa._group_lines(pdf.pages[0].extract_words(keep_blank_chars=False))
    keys = {
        "iniziale": "Saldo iniziale",
        "accrediti": "Totale accrediti",
        "addebiti": "Totale addebiti",
        "finale": "Saldo finale",
    }
    out: dict = {}
    for _top, ws in lines:
        joined = " ".join(w["text"] for w in ws)
        for k, label in keys.items():
            if label in joined:
                # ricompone l'amount dai token a destra (x0>=440)
                raw = "".join(intesa._clean(w["text"]) for w in ws if w["x0"] >= 440)
                raw = raw.replace("€", "").replace(" ", "")
                if raw:
                    out[k] = parse_money(raw, thousands_sep=".", decimal_sep=",")
    return out


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DIR
    files = sorted(glob.glob(str(Path(base) / PATTERN)))
    if not files:
        print(f"[ERRORE] nessun estratto trimestrale in {base}")
        return 2

    all_keys: list[str] = []
    files_ok = 0
    for f in files:
        txs = intesa.parse(f)
        acc = sum((t.amount for t in txs if t.amount > 0), Decimal("0"))
        add = sum((t.amount for t in txs if t.amount < 0), Decimal("0"))
        s = _summary(f)
        all_keys.extend(t.natural_key for t in txs)

        checks = []
        if "accrediti" in s:
            checks.append(abs(acc - s["accrediti"]) <= CENT)
        if "addebiti" in s:
            checks.append(abs(add - s["addebiti"]) <= CENT)
        if "iniziale" in s and "finale" in s:
            checks.append(abs(s["iniziale"] + acc + add - s["finale"]) <= CENT)
        ok = bool(checks) and all(checks)
        files_ok += ok
        flag = "OK " if ok else "ERR"
        print(f"  {flag} {Path(f).name[:36]:36} tx={len(txs):3d} acc={acc:>10} add={add:>11}")

    uniq = len(set(all_keys))
    dup = len(all_keys) - uniq
    print(f"\nFile riconciliati: {files_ok}/{len(files)}")
    print(f"Transazioni totali: {len(all_keys)} | uniche: {uniq} | dedup ai bordi: {dup}")
    print("\nESITO:", "OK" if files_ok == len(files) else "DA VERIFICARE")
    return 0 if files_ok == len(files) else 1


if __name__ == "__main__":
    raise SystemExit(main())
