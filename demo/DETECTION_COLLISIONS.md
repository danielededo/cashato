# Detection design: why markers must be bank-specific

Content-based detection routes an uploaded file to a parser by matching marker
groups against the file's head text. The tempting shortcut — generic banking
vocabulary as markers — silently steals other banks' documents. This document
is the worked example behind the rules in `src/cashato/parsers/detect.py` and
the contributor checklist in CONTRIBUTING; the synthetic files under
`demo/other_banks/` are its regression test (their pinned expectation is
`None`, unclaimed).

## How detection works

`src/cashato/parsers/detect.py` extracts a lowercased "head text" (CSV: first
4096 bytes; PDF: **page 1 text only**; XLSX: the first 26 rows via openpyxl —
`_xlsx_head` breaks on `i > max_rows` with `max_rows=25`, so indices 0–25.
`.xls` is not read at all: `head_text` handles only those three suffixes and
returns `None` for anything else, deliberately, since uploads reject the legacy
binary format outright).
A source matches if, for ANY of its `DETECTION` groups, ALL markers in that
group appear in the head text. **Every source is scored and the most specific
match wins** (specific = matched more markers); a tie at the top is reported
as AMBIGUOUS (`None`) rather than resolved by registry order, which is an
implementation accident (alphabetical module discovery), not a contract.

Intesa's groups are deliberately narrow — two, both Intesa-specific:

```python
DETECTION: list[list[str]] = [
    ["intesa sanpaolo"],   # page-1 footer of every quarterly statement
    ["conti e carte"],     # filter-recap header of the 13-month web export
]
```

All real Intesa quarterly statements carry "App Intesa Sanpaolo Mobile" in the
page-1 footer; the 13-month exports (PDF + XLSX) both carry the "Conti e
Carte" recap header. Generic markers (`estratto conto`, `dettaglio movimenti`,
`data contabile`, `["operazione","importo"]`) would match **no Intesa file the
specific groups miss** — they only add false positives.

## The collision catalogue

What generic Italian-banking markers would steal. Evidence: the synthetic
files in `demo/other_banks/`, whose text is faithful to the real formats
(layout contracts taken verbatim from third-party open-source parsers of
those banks).

1. **ING "Conto Arancio" quarterly statement PDF.** The real document
   necessarily contains `Estratto conto trimestrale al dd/mm/yyyy` (the
   third-party parser `g-gg/estratto_ing` requires it verbatim) and the title
   `LISTA MOVIMENTI`. File: `demo/other_banks/ing_estratto_trimestrale.pdf`.
2. **Hype (Banca Sella) movements PDF.** The movements table header contains
   `Data Contabile` (the column set the `ofxstatement-hype` parser is built
   around). File: `demo/other_banks/hype_lista_movimenti.pdf`.
3. **Widiba XLSX** — the real export title is `Lista Movimenti` (per its
   ofxstatement plugin).
4. **Webank ".xls"** — the real header contains `Data Contabile`. (The file is
   actually an HTML table with an .xls extension, but that never comes up:
   `head_text` does not handle `.xls` at all and returns `None` before anything
   is parsed. openpyxl is never invoked, so this one escapes for a second,
   structural reason — and `allowed_extensions` would reject the upload anyway.)
5. **Near-miss class — word-pair groups like `["operazione", "importo"]`.**
   Several real Italian exports carry one of the two words in the header and
   can pick up the other from any CELL in the head window: UniCredit format-2
   CSV has `Importo (EUR)` in the header; BPER's XLS header has
   `Data operazione`. One transaction description containing the missing word
   flips the file.

## Why misrouting is worse than no match

A misrouted file goes to a parser that finds none of its table anchors and
yields **0 transactions**. Without a guard, that would land in bronze as
`parsed` with `rows_new=0` — no error, nothing to alert on. Two defences are
in place:

- `cli/load.py` marks a file `failed` with an explanatory error when a parse
  yields zero transactions, instead of storing an empty parse as success.
- The upload-time explicit `source` override remains the manual escape hatch.

## Rules when adding a parser

1. **Markers must be bank-specific.** Bank name, product name (`"conto
   arancio"`, `"hype"`, `"widiba"`) — never generic vocabulary (`estratto
   conto`, `lista movimenti`, `data contabile`) that any Italian bank prints.
2. **Word-pair groups are a last resort** — any cell in the head window can
   supply the missing word (collision class 5 above).
3. **Never rely on registry order.** Scoring makes specificity explicit; a tie
   means the file is reported ambiguous, not silently claimed.
4. **Pin the negative case.** Add a synthetic look-alike document to
   `demo/other_banks/` whose expected detection is `None` — that pin is what
   keeps the next parser honest.
5. An IBAN's ABI code (`base.abi_from_iban`) can confirm a match where the
   document prints one, but cannot anchor detection alone: web exports (e.g.
   Intesa's 13-month) may print no IBAN at all.

## Repro

```bash
.venv/bin/python demo/generate.py   # regenerates demo/ and verifies:
# every other_banks/ file must report  detect -> None (unclaimed)
# every cashato-source file must report its own source
```
