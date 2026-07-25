# Contributing

Thanks for your interest! cashato normalizes and categorizes bank transactions
**locally** and in a **provider-agnostic** way.

## Dev setup

```bash
make venv
make install              # editable install: cashato package + svc deps + dev tools
./.venv/bin/pre-commit install
docker run -d --name cashato-pg -p 5432:5432 \
  -e POSTGRES_USER=cashato -e POSTGRES_PASSWORD=cashato -e POSTGRES_DB=cashato postgres:17-alpine
make migrate
```

## Workflow

```bash
make lint        # ruff check
make fmt         # ruff format + autofix
make typecheck   # mypy
make test        # pytest (unit tests, no DB/data needed)
```

Before opening a PR: `make lint && make test` must pass.

> CI/CD (phase C): **Tekton** (lint/test/build + ML pipeline) + **Argo CD**
> (GitOps deploy), self-hosted on the cluster — not GitHub Actions.

## Conventions

- **Python 3.12+**, max line length 100 (ruff / `.editorconfig`).
- Monetary amounts are **always `Decimal`**, never `float` (see `src/cashato/parsers/base.py`).
- **No bank data in the repo**: `data/`, `output/`, `models/` are git-ignored.
- **Code in English** (comments, docstrings, identifiers). Italian is allowed only
  in string literals that must match real (Italian) document text (detection
  markers, skip regexes, native seed keys).
- **Categories = language-neutral codes**; per-language labels live in
  `config/categorie.yaml` (add a language = add a key, no code change).
- **Provider-agnostic**: do not add runtime dependencies on providers' native
  taxonomies; use universal signals (MCC, text) + ML.

## Add a new source/bank (for forks & contributors)

The design is built so adding a bank touches **one adapter module**, nothing else
(`SOURCE_NAMES`, detection and the registry all auto-derive from the parser modules):

1. **Create `src/cashato/parsers/<name>.py`** exposing:
   - `parse(path) -> list[Transaction]` — the adapter;
   - `DETECTION: list[list[str]]` — content marker groups used to auto-detect the
     file (a file matches if, for ANY group, ALL its markers are in the head text);
   - `CURRENCY` — e.g. `"EUR"`.

   and, optionally, the account holder (shown as the greeting on the home page):
   - `extract_holder(path) -> str | None` — usually a one-liner over
     `base.addressee_from_words(pdf.pages[0].extract_words())`, which anchors on
     the CAP line of the addressee block. Return `None` for formats that carry no
     addressee (CSV/XLSX exports): unknown is a normal outcome, not an error;
   - `NAME_ORDER` — `base.GIVEN_FIRST` or `base.FAMILY_FIRST`, i.e. whether that
     source's documents write "DANIELE ROSSI" or "ROSSI MARIO".
     Declaring the *document's* convention is what lets the API pick out the first
     name without guessing which token is the surname.

   The module name **is** the source id; the registry auto-discovers it by scanning
   the package (no config entry, no edit to the loader). **Inspect a real file first**;
   don't guess the layout.
2. **Reuse the toolkit**: `base.parse_money`, `normalize_desc`,
   `assign_occurrence_keys`, and (for PDFs) the position-aware helpers as a model.
3. **Add a verification** in `tests/` (reconcile against the statement's declared
   totals when available) and unit tests where useful.

No changes to the core, the services, or the categorizer are needed: the new
source flows through detection → parse → dedup → categorization automatically.

To distribute your customization, **fork** the repo on GitHub and keep your
adapters in your fork (or open a PR to contribute a bank upstream). We keep
extension deliberately simple — config + one module, no plugin machinery.

## Add a language to the categories

Add the language key (e.g. `es:`) to the entries under `categories:` in
`config/categorie.yaml`. No code change.
