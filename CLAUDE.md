# Project: cashato — personal bank-transaction data platform

> **Original intent (genesis).** This started as a narrow task: build Python
> parsers for three heterogeneous sources (Revolut, Trade Republic, Intesa
> Sanpaolo) and normalize their transactions into one common schema, classified
> into income/expense and by category, to get a clear view of spending across
> accounts. That goal still holds — it is now the **silver/gold** core of a
> larger, cloud-native platform the project grew into (a deliberate learning
> vehicle). Two constraints from day one are non-negotiable: **use `Decimal`,
> never `float`** for money, and **no bank data leaves the machine — all
> processing is local**.

This file is the authoritative project guide. Keep it in sync with the code.
Detailed status/decisions live in the memory (`~/.claude/.../memory/`) and the
plan (`~/.claude/plans/leggi-il-file-claude-md-crispy-pixel.md`).

---

## What it is now

A local, provider-agnostic pipeline that ingests bank statements (any of several
formats per source), normalizes them into a common schema, deduplicates across
formats/sources, categorizes them with an ML model (not provider taxonomies),
detects internal transfers, and exposes spending aggregates via APIs — packaged
as microservices, heading toward a Kubernetes (kind) platform, all as IaC.

**Phases:** A (data core) and B (adapters + microservices + ML) are **done and
verified**. **Phase C (platform, IaC) is next.** Then E (frontend SPA) and D
(CDC, optional). See the plan for the full breakdown.

---

## Architecture (medallion, in one Postgres)

Schemas within a single database (not separate DBs):

- **bronze** — `raw_files` (uploaded file registry: sha256 UNIQUE, status
  `pending|parsed|failed`, `rows_total`, `rows_new`, `error`). Raw row landing
  (`raw_rows`) was intentionally dropped (YAGNI): reprocessing uses the retained
  file + sha256.
- **silver** — `transactions`, the common normalized schema (below). Idempotent
  upsert `ON CONFLICT (natural_key) DO NOTHING`.
- **gold** — read-only views for the query API: `v_category_totals`,
  `v_income_expense_month`, `v_category_month`, `v_internal_transfers`,
  `v_transactions` (projection of silver so the read API stays gold-only). Plus
  ML tables `training_labels`, `category_feedback` (active learning).

Migrations: Alembic (`db/migrations`), currently through **0009**.

### Common schema (`silver.transactions`) — English

| Field                | Type       | Notes |
|----------------------|------------|-------|
| `value_date`         | date       | value date (used in the dedup key) |
| `booking_date`       | date       | booking date (may equal value date) |
| `description`        | str        | raw operation text |
| `amount`             | Decimal    | **signed**: negative = outflow, positive = inflow |
| `currency`           | str (ISO)  | e.g. `EUR` |
| `account`            | str        | account/source id (e.g. `revolut_personal_eur`) |
| `source`             | str        | `revolut` \| `trade_republic` \| `intesa` |
| `category`           | str \| null| language-neutral **code** (labels live in `categorie.yaml`) |
| `category_source`    | str        | `mcc` \| `model` \| `rule` \| `manual` \| `default` |
| `category_confidence`| real       | provenance/confidence of the category |
| `native_category`    | str \| null| provider's own category — bootstrap-only, **never** used at runtime |
| `mcc`                | str \| null| ISO 18245 merchant category code when available |
| `transfer_group`     | str \| null| shared id of the two legs of an internal transfer |
| `natural_key`        | str UNIQUE | canonical dedup key |

Income vs expense is derived from the **sign** of `amount`, not a separate column.

### Canonical dedup key (recognize already-reconciled transactions)

`natural_key = sha256(account, value_date, amount, occurrence_index)`.
The **description is excluded** (it differs across formats), so the same movement
imported from PDF vs CSV, or from overlapping/edge-of-quarter exports, yields the
same key and is deduped automatically. `amount` is quantized to 2 decimals; for
the Trade Republic CSV `amount = amount + fee + tax` (net account impact, like the
PDF). The occurrence index (`assign_occurrence_keys`) disambiguates genuinely
identical same-day operations.

---

## The three sources (multi-format)

Each source accepts more than one format; routing is content-based
(`libs/parsers/detect.py`, no filename guessing) with an optional explicit
`source` override at upload. Inspect a real file before writing/adjusting a parser
— never guess a PDF layout.

- **Revolut** — consolidated-statement **CSV** *and* **PDF**. Not the flat format
  once assumed: sections per currency (`Date, Description, Category, Money in/out,
  Balance …`), amounts with `€`/thousands, no `State` column. **EUR only** (other
  currencies ignored). Crypto/savings-interest sections → dedicated accounts and
  `crypto`/`investments` categories.
- **Trade Republic** — statement **PDF** *and* transaction-export **CSV**.
  Position-aware PDF parsing. Deposits/withdrawals/card payments = cash flow;
  securities/dividends → `investments`.
- **Intesa Sanpaolo** — 21 quarterly statement **PDFs** *and* a 13-month
  **PDF/XLSX** export. Italian statement layout: dare/avere → reconstruct the
  sign, double date, multi-line rows, skip headers/balances. Concatenate all
  files + dedup via `natural_key`.

PDFs may be in **Italian or English** — parsers use position-aware extraction +
bilingual maps for months/headers/operation types.

---

## Categorization (provider-agnostic, i18n)

Never depend on providers' native categories (inconsistent, often absent, biasing).
Resolver chain (order = priority) over **universal** signals:

1. **MCC** (ISO 18245, `config/mcc.yaml`) — exact, high precision.
2. **Embedding model** — `EmbeddingKNN` (`ml/model.py`): multilingual
   sentence-transformers + kNN; used if `confidence ≥ threshold`. Feature text =
   `normalize_desc` only (no regex cleaning). This does the bulk of the work.
3. **Rules** — thin bilingual keyword safety net (`config/categorie.yaml`).
4. Default `other`.

`category` is a language-neutral **code**; per-language labels live in
`config/categorie.yaml` (add a language = add a key, no code change). Native
categories are at most an opt-in training-bootstrap signal, off by default.

**ML flow (offline, local):** Ollama (host, GPU) labels the long tail →
`gold.training_labels` → `EmbeddingKNN` trained → recategorize. `other` dropped
~52% → ~14%. Ollama is **not** in-cluster (labeling-time only). In phase C:
MLflow (registry, + MinIO) + KServe (serving); a separate **categorizer** service
does model categorization off an event, keeping the etl-worker light.

## Internal transfers

`libs/transfers.py` pairs opposite-amount legs on different own-accounts within a
window (`transfers.window_days`), guarded by same-day OR a transfer hint; tags
both legs with `transfer_group`. Gold spend views **exclude** these (they net to
zero, not spending). `link_transfers.py` runs the batch.

---

## Services & API

FastAPI microservices; NATS JetStream backbone. Probes at root (`/healthz`,
`/readyz`), business API under `/api/v1`, `ROOT_PATH` for the gateway, OpenAPI at
`/docs`. Structured JSON logging + Prometheus `/metrics` (`libs/obs.py`).

- **ingest-api** — `POST /uploads` (stores file, validates extension → 415 and
  per-file size cap → 413, enqueues a NATS job); `GET /files` (status +
  `rows_new`/`rows_duplicate`/`error`); `POST /feedback` (category correction →
  NATS event; a **write**, so it lives here, not in the read-only query-api).
- **etl-worker** — consumes `ingest.jobs` (detect → parse → normalize → dedup →
  persist + fast-path category) and `category.feedback` (apply correction to
  silver + record in `gold.category_feedback`). Stays light (no torch/model).
- **query-api** — read-only over gold: `GET /summary`, `/monthly`,
  `/categories/monthly`, `/transactions` (filterable/paginated), `/transfers`.
  `?lang=it|en` for category labels.

---

## Extensibility, config, stack

- **Add a source** (fork/monorepo model, no plugin machinery): add an entry to
  `config/sources.yaml` + a `libs/parsers/<name>.py` exposing `parse(path) ->
  list[Transaction]` (module name == source name; the registry auto-wires it).
  See CONTRIBUTING.
- **Parametrized**: `config/sources.yaml` (sources + detection + currency),
  `config/settings.yaml` (thresholds, embed model, transfer window, upload caps),
  `config/categorie.yaml`, `config/mcc.yaml`. These become ConfigMaps in phase C;
  infra endpoints/secrets stay env/Secret.
- **Stack**: Python 3.12, Postgres 17, SQLAlchemy + psycopg + Alembic,
  pdfplumber, pandas, sentence-transformers (CPU), NATS, FastAPI, ruff + mypy +
  pytest, MIT license. Dev via `deploy/docker-compose.yml` (postgres + nats +
  services). Everything in English (Italian only in string literals that must
  match real document text).

## Key decisions

- Medallion = schemas in one Postgres. Dedup by canonical key (description
  excluded). Income/expense by sign. Provider-agnostic categorization.
- CI/CD: **Tekton + Argo CD** (not GitHub Actions). Registries: **MLflow**
  (models), **Harbor** (images/Helm). Secrets: **Sealed Secrets**. Code on GitHub.
- Platform (phase C): kind + Cilium + CNPG + Envoy Gateway + NATS, all IaC (tool
  to pick: OpenTofu vs Pulumi-Python), DB roles least-privilege, LGTM observability.
- Multi-user/household is future work (RLS + OIDC), not yet built.
