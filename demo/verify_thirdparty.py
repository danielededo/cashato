#!/usr/bin/env python
"""Verify the synthetic other-bank PDFs against the REAL third-party parsers.

The Poste and ING PDFs in other_banks/ are generated to the layout contracts
of two open-source parsers; this script runs those actual parsers on them.
It needs a separate environment (NOT the project .venv):

    python -m venv /tmp/pdfverify && /tmp/pdfverify/bin/pip install \
        "pymupdf==1.24.7" pypdf pandas openpyxl
    git clone https://github.com/genbs/poste-italiane-parser <parsers>/poste-italiane-parser
    git clone https://github.com/g-gg/estratto_ing           <parsers>/estratto_ing
    /tmp/pdfverify/bin/python demo/verify_thirdparty.py --parsers-dir <parsers>

(The Hype PDF's parser, ofxstatement-hype, needs tabula-py + a Java runtime,
so it is not exercised here.)

Locale shims: both parsers require an it_IT locale at import/parse time; on
machines without it we no-op setlocale (only needed for Italian month names
the demo does not emit) and give locale.atof an Italian-format fallback.
"""

import argparse
import locale
import sys
from pathlib import Path

locale.setlocale = lambda *a, **k: "C"  # shim: it_IT locale may not be installed
locale.atof = lambda s: float(str(s).replace(".", "").replace(",", "."))

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("--parsers-dir", required=True, type=Path,
                help="directory containing the cloned poste-italiane-parser and estratto_ing repos")
ap.add_argument("--demo-dir", default=Path(__file__).parent / "other_banks", type=Path)
args = ap.parse_args()

failures = 0


def check(cond: bool, msg: str) -> None:
    global failures
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    failures += 0 if cond else 1


# --- Poste (genbs/poste-italiane-parser, PyMuPDF, coordinate-based) -------------
sys.path.insert(0, str(args.parsers_dir / "poste-italiane-parser"))
from poste_italiane_parser import PosteItalianeParser  # noqa: E402

data = PosteItalianeParser(str(args.demo_dir / "bancoposta_estratto_conto.pdf"))
check(data["document_type"] == "ESTRATTO_CONTO", f"poste: document_type = {data['document_type']}")
txs = data["transactions"]
check(len(txs) > 15, f"poste: {len(txs)} transactions parsed")
check(all(t["value"] is not None for t in txs), "poste: every row has a value")
check(bool(data.get("holder")) and "MARIO" in str(data.get("holder")).upper(),
      f"poste: holder = {data.get('holder')!r}")

# --- ING (g-gg/estratto_ing, pypdf, line-based state machine) --------------------
sys.path.insert(0, str(args.parsers_dir / "estratto_ing"))
from parse_estratto_ing import parser as ing_parser  # noqa: E402

p = ing_parser(str(args.demo_dir / "ing_estratto_trimestrale.pdf"))
p.parse()
check(p.state == "DONE", f"ing: final state = {p.state} (DONE = balance check passed)")
check(len(p.operations) > 8, f"ing: {len(p.operations)} operations parsed")

sys.exit(1 if failures else 0)
