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

Fixtures follow the repo privacy rule: `MARIO ROSSI` and checksum-invalid
IBANs only.

## Manual verification (`verify_*.py`)

**Not tests** — pytest ignores them (see `addopts` in `pyproject.toml`). They
are CLI tools that reconcile each adapter against **your real statements** in
`data/` (declared totals on the document vs the parsed sum, to the cent), then
measure cross-file dedup:

```bash
./.venv/bin/python tests/verify_intesa.py [directory]
./.venv/bin/python tests/verify_revolut.py
./.venv/bin/python tests/verify_trade_republic.py
```

Run them after touching a parser, before trusting the numbers.
