# demo/ — synthetic demo dataset

Fully **fictional** bank statements (persona: *Mario Bianchi*, Milano) covering
every format the three adapters support. Safe to commit and share: no real bank
data is involved (the real data lives in `data/`, which is gitignored).

Regenerate any time with:

```bash
.venv/bin/python demo/generate.py        # writes demo/ + verifies
```

The generator is deterministic (seeded RNG): the same seed always produces the
same files, so these double as **test fixtures**.

## How it was built (the method)

1. **Reverse-engineer the format contract from the parsers, not from samples.**
   Parsing is position-aware, so each renderer places words at the exact X
   coordinates the parser is calibrated on (e.g. Intesa: booking date `x<70`,
   value date `85–150`, description `150–360`, amounts `≥360` with the
   debit/credit split derived from the `Accrediti` header; Trade Republic:
   column geometry derived from the header words' right edges, so amounts are
   right-aligned under their header). Detection is content-based, so each file
   embeds its source's `DETECTION` markers — and avoids the *other* sources'
   markers (detection runs in registry order: intesa → revolut →
   trade_republic; that is why the TR statement is titled "RENDICONTO", not
   "Estratto conto").
2. **One ground truth, many renderings.** An 18-month synthetic financial life
   (salary, rent, utilities, groceries, subscriptions, ETF savings plan,
   internal transfers) is generated once, then rendered into all six formats.
   Since `natural_key = sha256(account | value_date | amount | occurrence)`,
   files that share movements dedup automatically no matter the format.
3. **Verify with the real parsers.** The script ends by running
   `detect_source` + each adapter on the generated files and asserting
   51 checks (detection, in-file key uniqueness, cross-format dedup,
   occurrence index, holder/account extraction, transfer legs, and
   non-misdetection of the `other_banks/` files).

## Files

| File | Format | What it exercises |
|---|---|---|
| `intesa_estratto_conto_{2025_Q1..2026_Q2}.pdf` | quarterly statement PDF | double date, sign from debit/credit column, multi-line descriptions, skip rows (`Saldo…`, `Pagina…`), addressee block (surname-first), IBAN + `Tipologia conto` |
| `intesa_lista_operazioni_13m.xlsx` | 13-month export | **overlaps the quarterlies** → cross-file dedup via `natural_key` |
| `revolut_consolidated_statement.csv` | consolidated CSV | per-account sections (Personal + **Joint**), 8-column tx tables, account-details tables (`Holding modalities`), savings-interest + crypto-sales sections, fee → separate fee transaction |
| `revolut_consolidated_statement.pdf` | same statement, PDF | **same cash movements as the CSV** → cross-format dedup (PDF ⊂ CSV; the 20 CSV-only keys are interest+crypto) |
| `trade_republic_rendiconto.pdf` | statement PDF (Italian) | header-derived column geometry, balance-anchored rows, split dates (`04 gen 2025`), sign from IN ENTRATA / IN USCITA column |
| `trade_republic_transactions.csv` | transaction export | `amount + fee + tax` = the PDF's net amount → **identical key sets** with the PDF |
| `expected_transactions.csv` | ground truth | the 601 unique silver rows expected after ingesting *all* files (1111 parsed rows − 510 dedup) |

## Deliberate edge cases

- **Occurrence index**: two identical POS payments (−1,20 €) on 2025-03-14 →
  two distinct `natural_key`s inside one file, still stable across formats.
- **Internal transfers**: monthly Intesa→Revolut (−300/+300, 1 day apart) and
  Intesa→Trade Republic (−400/+400) — opposite legs within the transfer window,
  for `transfer_group` pairing.
- **Fee transaction**: a Revolut ATM withdrawal with a 2,00 € fee (2025-08-09)
  → the adapter emits a separate `Fee:` transaction.
- **value date ≠ booking date** on some Intesa POS rows (the XLSX lists by
  value date — that is what makes the overlap dedup work).

## Layout gotchas encoded in the renderers

- Bank letterheads are single-line **on purpose**: a bank address ending in a
  CAP line would be picked up by `addressee_from_words` as the account holder.
- The Intesa movements header is repeated **on every page** (pages without it
  are skipped by the parser); the Trade Republic header appears **once**, and is
  followed by a balance-only `Saldo iniziale` row that absorbs the header words
  into a discarded block (mirroring the real layout).
- The Intesa IBAN is structurally valid (ABI 03069) so `find_iban` /
  `abi_from_iban` work; the Revolut/TR IBANs are foreign and intentionally
  ignored by the Italian-only `find_iban`.

## other_banks/ — future sources

Synthetic export files for banks named in `config/banks.yaml` that cashato has
**no parser for yet** — ready-made inputs for the "Add a source" workflow.
Formats replicated **verbatim from public, anonymized references** (never from
real data): the [BananaAccounting/Italia](https://github.com/BananaAccounting/Italia)
import-extension testcases and the sources of the ofxstatement plugins
([fineco](https://github.com/frankIT/ofxstatement-fineco),
[widiba/webank](https://github.com/ecorini/ofxstatement-it-banks)).

| File | Reference | Format quirks reproduced |
|---|---|---|
| `unicredit_movimenti_format1.csv` | Banana testcase (UTF-8 BOM variant) | 2-row preamble (`Rapporto <IBAN> - <holder>`), header **split across two rows** (`Data;;Descrizione;EUR;Caus.` + `Operaz.;Valuta;;;`), dd/mm/yyyy, Italian amounts, 3-digit causale codes |
| `unicredit_movimenti_format2.csv` | Banana testcase | header with **trailing `;`** (empty 5th column), dd.mm.yyyy, descriptions padded in 50-char runs |
| `bper_smart_web_movimenti.xlsx` | Banana testcase (tab-converted XLS) | 11 preamble rows (saldi block, "Stai visualizzando…"), header at row 12, **Italian long-form dates** ("30 aprile 2026"), Entrate/negative Uscite, `Totale` + "Dati Aggiornati al" footer |
| `popso_scrigno_movimenti.csv` | Banana testcase ("Scrigno" format 2) | every field double-quoted, **explicit +/- sign**, dot decimal, no thousands separator |
| `fineco_movimenti.xlsx` | ofxstatement-fineco test workbook (cell-level dump) | sheet `Movimenti`, account id in A1 after `Conto Corrente: `, 6 preamble rows, header at row 7, Entrate XOR Uscite, `Totale` footer, `Autorizzato`/`Contabilizzato` |
| `widiba_movimenti.xlsx` | ofxstatement-it-banks source | columns **A and F empty**, account number in **cell D10**, booking date as raw **Excel serial number**, `Totale (€)` marker row |
| `webank_movimentiConto.xls` | ofxstatement-it-banks source | an **HTML table** despite the .xls extension (parsed with `pandas.read_html`, decimal=`,`); only the 3 plugin-confirmed columns |
| `bancoposta_estratto_conto.pdf` | [poste-italiane-parser](https://github.com/genbs/poste-italiane-parser) (PyMuPDF, coordinate-based) | cells matched by span **right edge** in per-column x-windows, page-1 metadata areas (holder, "Euro", IBAN, account digits), SALDO INIZIALE/FINALE **balance check**, Italian amounts without `€` — **verified by running the real parser** (`verify_thirdparty.py`) |
| `hype_lista_movimenti.pdf` | [ofxstatement-hype](https://github.com/lorenzogiudici5/ofxstatement-hype) (tabula stream mode) | 6 whitespace-separated columns, amount `-12,34€` with leading sign AND trailing `€` (the parser slices both), Tipologia from its startswith-map — **not runtime-verified** (tabula needs a Java runtime, absent here) |
| `ing_estratto_trimestrale.pdf` | [estratto_ing](https://github.com/g-gg/estratto_ing) (pypdf, line state machine) | exact `Estratto conto trimestrale al dd/mm/yyyy` line, `USCITE` header + `RECT_` footer per page, `<data> <importo> € <TIPO> - <desc>` rows with TIPO from a closed list and per-type description shapes, balance to the cent — **verified by running the real parser** |

Notes:
- All these exports list movements **newest first** (like the real ones).
- Descriptions deliberately avoid the word "operazione": several headers already
  contain "importo", and the pair would trip Intesa's content detection. The
  generator pins what `detect_source(...)` returns for each file — when you add
  one of these parsers, its `DETECTION` groups must stay distinguishable from
  Intesa's generic markers (and note that Webank's real header "Data Contabile"
  IS an Intesa marker: it only escapes because openpyxl cannot open HTML).
- Widiba's real export title "Lista Movimenti" also collides with an Intesa
  marker; the synthetic file uses "Movimenti del conto" instead.
- **Known REAL detection collisions** (kept faithful, pinned as `intesa` in the
  verify step): the ING statement necessarily contains "Estratto conto
  trimestrale" and "LISTA MOVIMENTI", and the Hype table header contains "Data
  Contabile" — all Intesa markers. A genuine ING/Hype PDF uploaded today would
  be misrouted to the Intesa parser (which then finds no table and yields 0
  rows). Worth revisiting Intesa's generic markers before adding these sources.
- The Poste and ING PDFs are additionally verified by **running the actual
  third-party parsers** on them — see `verify_thirdparty.py` for how (separate
  venv; not part of `generate.py`'s standard checks).

To load the dataset into the platform, upload the 11 statement files via the
frontend Upload page (or `POST /uploads`); `expected_transactions.csv` is the
assertion baseline, not an input. The `other_banks/` files are NOT ingestable
yet (uploading one exercises the unknown-source failure path).
