# Finding: Intesa's content-detection markers collide with other Italian banks' documents

Status: **FIXED (2026-07-25)**. Kept as the record of the problem and of why the
fix is the shape it is. The demo files under `demo/other_banks/` are now the
regression test: their pinned expectation is `None` (unclaimed), and it was
`intesa` before.

**What was done.** Intesa's detection was cut from six marker groups to two, both
Intesa-specific. The decisive measurement: all 21 real quarterly statements
already match the specific `["intesa sanpaolo"]` group — their page-1 footer
carries "App Intesa Sanpaolo Mobile" — so `estratto conto`, `dettaglio
movimenti`, `data contabile` and `["operazione","importo"]` were matching **no
real Intesa file that the specific groups did not already cover**, while
stealing other banks' documents. They were pure liability, not a trade-off.

The two 13-month exports (PDF + XLSX) were the only files needing a second
group; they are covered by `["conti e carte"]`, the filter-recap header of
Intesa's own web export, present in both and in none of the ten other-bank
files. Verified: 23/23 real Intesa files still detected, 0 false positives,
Revolut and Trade Republic unaffected.

Recommendation 1 below (anchor on the IBAN's ABI) was **checked and rejected as
the primary fix**: neither 13-month export prints an IBAN, so ABI cannot anchor
them. The document flagged that as an open caveat; it is now settled.

Recommendation 2 (guard the 0-rows outcome) was **also implemented**, as defence
in depth — `cli/load.py` now marks a file `failed` with an explanatory error when
a parse yields zero transactions, instead of storing an empty parse as success.

Recommendations 3 and 4 remain open and still apply to whoever adds a parser.

---

## Original briefing (2026-07-25, before the fix)


## How detection works today (context)

`src/cashato/parsers/detect.py` routes an uploaded file by content: it extracts
a lowercased "head text" (CSV: first 4096 bytes; PDF: **page 1 text only**;
XLSX/XLS: first 25 rows via openpyxl) and walks the sources in **registry
order, first match wins**. A source matches if, for ANY of its `DETECTION`
groups, ALL markers in that group appear in the head text. Registry order is
alphabetical module discovery (`registry.py`): today `intesa` → `revolut` →
`trade_republic`, so Intesa is tried first.

Intesa's groups (`src/cashato/parsers/intesa.py`) are, by necessity, generic
Italian banking vocabulary — the code comment on `extract_accounts` explains
why: *the quarterly statement never names the bank*, so there is no
Intesa-specific string to anchor on:

```python
DETECTION: list[list[str]] = [
    ["intesa sanpaolo"],
    ["estratto conto"],
    ["dettaglio movimenti"],
    ["lista movimenti"],
    ["data contabile"],
    ["operazione", "importo"],  # Intesa 13-month XLSX header
]
```

Five of the six groups are not Intesa-specific at all.

## Confirmed collisions

Evidence: the synthetic files in `demo/other_banks/`, whose text is faithful to
the real formats (layout contracts taken verbatim from third-party open-source
parsers of those banks). Reproduce with `.venv/bin/python demo/generate.py`
(the verify step pins these outcomes).

1. **ING "Conto Arancio" quarterly statement PDF → detected as `intesa`.**
   The real document necessarily contains the line
   `Estratto conto trimestrale al dd/mm/yyyy` (the third-party parser
   `g-gg/estratto_ing` requires it verbatim) and the title `LISTA MOVIMENTI` —
   matching TWO Intesa groups. There is no faithful way to avoid it.
   File: `demo/other_banks/ing_estratto_trimestrale.pdf`.

2. **Hype (Banca Sella) movements PDF → detected as `intesa`.**
   The movements table header contains `Data Contabile` (it is the column set
   the `ofxstatement-hype` parser is built around).
   File: `demo/other_banks/hype_lista_movimenti.pdf`.

3. **Widiba XLSX — real export title is "Lista Movimenti"** (per its
   ofxstatement plugin), an Intesa marker. The synthetic file dodges it by
   using a different title, but a REAL Widiba export would collide.

4. **Webank ".xls" — real header contains `Data Contabile`**, an Intesa marker.
   It escapes today only by accident: the file is an HTML table with an .xls
   extension, openpyxl throws, head text is `None`, detection returns `None`.
   That is luck, not design.

5. **Near-miss class — the `["operazione", "importo"]` pair.** Several real
   Italian exports carry one of the two words in the header and can pick up
   the other from any cell in the head window: UniCredit format-2 CSV has
   `Importo (EUR)` in the header (one description containing "operazione"
   within the first 4096 bytes flips it to Intesa); BPER's XLS header has
   `Data operazione` (any cell with "importo" in the first 25 rows flips it).
   The demo files avoid the word "operazione" in descriptions on purpose.

## Why it matters

- **Failure mode is silent.** A misrouted file goes to the Intesa parser,
  which finds no `Descrizione`/`Accred*` table header, returns **0
  transactions**, and the file lands in bronze as `parsed` with `rows_new=0` —
  no error, nothing to alert on. Worse than a hard failure.
- **Registry order will shift under your feet.** Discovery is alphabetical:
  future modules named `bper.py`, `fineco.py`, `hype.py`, `ing.py` would all be
  probed BEFORE `intesa.py`, silently changing who wins ties. Order is an
  implementation accident, not a contract.
- The upload-time explicit `source` override exists and remains the manual
  escape hatch, but detection is the default path.

## Recommendations (in order of leverage)

1. **Anchor Intesa on the IBAN's ABI code.** The one Intesa-specific signal the
   quarterly statement always carries is the IBAN, and `base.abi_from_iban`
   already decodes ABI `03069`. Detection could accept the generic markers only
   together with a positive ABI match (or downgrade generic-marker-only matches
   to a low-confidence result). This kills collisions 1–4 without touching the
   genuinely specific `["intesa sanpaolo"]` group. Caveat: the 13-month
   XLSX/PDF export may not print the IBAN — check a real one before relying on
   this for every format.
2. **Guard the 0-rows outcome.** Defense in depth regardless of markers: if the
   detected parser returns 0 transactions, treat the file as `unknown source`
   (fail the job / surface it in `/files`) instead of storing an empty parse as
   success. Cheap, catches every future collision.
3. **Make match specificity explicit rather than order-dependent.** E.g. score
   by marker specificity (multi-marker and bank-name groups beat single generic
   words) or require new sources' `DETECTION` to be checked before generic
   groups. At minimum, document that single generic-word groups are last-resort
   and that registry order is not a tie-breaking contract.
4. **When adding ING/Hype/Widiba parsers**, their `DETECTION` must include
   bank-specific strings (`"conto arancio"`, `"ing bank"`, `"hype"`, `"widiba"`)
   AND Intesa's generic groups must be tightened first — otherwise Intesa still
   steals their files whenever it sorts first.

## Repro

```bash
.venv/bin/python demo/generate.py            # look for the two lines:
#   PASS  detect other_banks/hype_lista_movimenti.pdf -> intesa (expected intesa)
#   PASS  detect other_banks/ing_estratto_trimestrale.pdf -> intesa (expected intesa)
```

(The `expected intesa` pins document the CURRENT misrouting on purpose; after
fixing detection, flip those expectations to the new behaviour.)
