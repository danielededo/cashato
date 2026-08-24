# tests/ — unit tests and manual verification

Two kinds of file live here; only the first is run by `pytest`.

## Unit tests (`test_*.py`)

Fast, no DB, no real data — safe anywhere (`make test` / CI):

| File | Covers |
|------|--------|
| `test_base.py` | money parsing (signs, currencies, parentheses), `natural_key` dedup, occurrence index, account-holder extraction from word layouts, IBAN/ABI |
| `test_categorize.py` | resolver-chain priority, detection scoring (Intesa markers must not claim other banks) |
| `test_transfers.py` | internal-transfer pairing rules (window, same-day/hint guard) |
| `test_messaging.py` | NATS settlement rules: what gets acked, nak'd, termed |
| `test_csrf_guard.py` | ingest-api cross-site write protection |
| `test_load_guard.py` | a parse yielding 0 rows marks the file failed, never "parsed, empty" |
| `test_merchant.py` | counterparty + time-of-day extraction per source; fails a registered source missing from `_BY_SOURCE` |
| `test_balances.py` | statement-declared anchors and the basis-aware reconciliation arithmetic |
| `test_recurrence.py` | recurring-movement detection: cadence, the consecutive-pair amount gate, lapsed vs active |
| `test_coverage.py` | per-source staleness scaled to anchor cadence, and holes in the covered-day union |
| `test_sort_guard.py` | the query-api sort whitelist (no SQL injection through an ORDER BY) |

Fixtures follow the repo privacy rule: `MARIO ROSSI` and checksum-invalid
IBANs only.

## Manual verification (`verify_*.py`)

**Not tests** — pytest ignores them (see `addopts` in `pyproject.toml`). They
are CLI tools that check each adapter against **your real statements** in
`data/`, and they do not all check the same thing:

- `verify_intesa.py` — reconciles each quarterly against the totals declared on
  page 1 (accrediti/addebiti, opening+closing balance) to the cent, then dedups
  the whole set to measure the quarter-boundary overlaps. The only one that does
  cross-file dedup;
- `verify_trade_republic.py` — reconciles the statement's summary box, plus
  in-file duplicate keys;
- `verify_revolut.py` — no declared total to reconcile against, so it checks row
  count plausibility, sign consistency, balance-chain continuity and in-file
  duplicate keys.

Each takes an optional path override as its first argument:

```bash
./.venv/bin/python tests/verify_intesa.py [directory]
./.venv/bin/python tests/verify_revolut.py [csv_path]
./.venv/bin/python tests/verify_trade_republic.py [pdf_path]
```

Run them after touching a parser, before trusting the numbers.
