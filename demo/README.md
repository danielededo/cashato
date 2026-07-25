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
   44 checks (detection, in-file key uniqueness, cross-format dedup,
   occurrence index, holder/account extraction, transfer legs).

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

To load the dataset into the platform, upload the 11 statement files via the
frontend Upload page (or `POST /uploads`); `expected_transactions.csv` is the
assertion baseline, not an input.
