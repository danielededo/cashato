# Contributing

Thanks for your interest! cashato normalizes and categorizes bank transactions
**locally** and in a **provider-agnostic** way.

## Dev setup

```bash
make venv
make install-dev          # runtime + tools (ruff, mypy, pytest, pre-commit)
./.venv/bin/pre-commit install
make db-up && make migrate
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
- Monetary amounts are **always `Decimal`**, never `float` (see `libs/parsers/base.py`).
- **No bank data in the repo**: `data/`, `output/`, `models/` are git-ignored.
- **Code in English** (comments, docstrings, identifiers). Italian is allowed only
  in string literals that must match real (Italian) document text (detection
  markers, skip regexes, native seed keys).
- **Categories = language-neutral codes**; per-language labels live in
  `config/categorie.yaml` (add a language = add a key, no code change).
- **Provider-agnostic**: do not add runtime dependencies on providers' native
  taxonomies; use universal signals (MCC, text) + ML.

## Add a new source/bank (for forks & contributors)

The design is built so adding a bank touches **config + one adapter module**,
nothing else (`SOURCE_NAMES`, detection and the adapter registry all derive from
config):

1. **Add the source to `config/sources.yaml`**: an entry `<name>` with `display`,
   `currency`, and `detection` (content marker groups used to auto-detect the file).
2. **Create `libs/parsers/<name>.py`** exposing `parse(path) -> list[Transaction]`.
   The module name **must equal** the source name (the registry auto-wires it —
   no edit to `load.py`). **Inspect a real file first**; don't guess the layout.
3. **Reuse the toolkit**: `base.parse_money`, `normalize_desc`,
   `assign_occurrence_keys`, and (for PDFs) the position-aware helpers as a model.
4. **Add a verification** in `tests/` (reconcile against the statement's declared
   totals when available) and unit tests where useful.

No changes to the core, the services, or the categorizer are needed: the new
source flows through detection → parse → dedup → categorization automatically.

To distribute your customization, **fork** the repo on GitHub and keep your
adapters in your fork (or open a PR to contribute a bank upstream). We keep
extension deliberately simple — config + one module, no plugin machinery.

## Add a language to the categories

Add the language key (e.g. `es:`) to the entries under `categories:` in
`config/categorie.yaml`. No code change.
