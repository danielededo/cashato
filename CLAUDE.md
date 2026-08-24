# Project: cashato — personal bank-transaction data platform

A local, provider-agnostic pipeline that ingests bank statements (several
formats per source), normalizes them into a common schema, deduplicates across
formats/sources, categorizes them with an ML model (not provider taxonomies),
detects internal transfers, and exposes spending aggregates via APIs — packaged
as microservices running on a Kubernetes (kind) platform, all as IaC.

Two constraints are non-negotiable: **use `Decimal`, never `float`** for
money, and **no bank data leaves the machine — all processing is local**.

This file is the authoritative project guide. Keep it in sync with the code.

---

## Architecture (medallion, in one Postgres)

Schemas within a single database (not separate DBs):

- **bronze** — `raw_files` (uploaded file registry: sha256 UNIQUE, status
  `pending|parsed|failed`, `rows_total`, `rows_new`, `error`, `account_holder`).
  There is no raw-row landing table: reprocessing uses the retained file +
  sha256.
- **silver** — `transactions`, the common normalized schema (below). Upsert on
  `natural_key`: KEY identity (amount/value date/account/occurrence) is
  immutable; the DESCRIPTION converges to the richest observed (strictly longer
  wins — twin formats carry the same movement with different text, so the
  surviving text must not depend on upload order), and the BOOKING date
  converges to the document that distinguishes it from the value date (the
  quarterly; the 13-month export has one date, so rows it inserts first carry a
  flattened booking). Category follows the text unless `manual`. Re-loading the
  same file is a no-op.
- **gold** — read-only views for the query API (twelve, all created in the
  baseline migration): `v_category_totals`, `v_income_expense_month`,
  `v_category_month`, `v_internal_transfers`, `v_transactions` (projection of
  silver so the read API stays gold-only), `v_transaction_detail` (the same
  movement plus how its category was assigned, the raw provider signals, the
  originating file and the instrument if it was a trade — the drawer behind a
  row), `v_balances`, `v_reconciliation`, `v_balance_month` (month-end declared
  balance per account, carried forward from the last anchor — feeds the
  wealth-over-time chart), `v_accounts` (account display metadata with the user
  override applied), `v_holdings` and `v_investment_month` (positions and
  contributions per month, behind `/investments`). Plus ML tables
  `training_labels`, `category_feedback` (active learning).

Silver holds four more tables next to `transactions`:

- `accounts` — everything a statement discloses about an account (bank,
  product, joint/individual, IBAN) plus `display_name_override`. Display
  metadata only: the account **id** is immutable because it is hashed into
  `natural_key`.
- `asset_categories` — which categories are *wealth, not spending*. A row here
  removes a category from every spend view, so it is reference data seeded by an
  INSERT in the baseline (currently `investments`, `crypto`, `pension_fund`,
  `deposits`, `insurance_savings`), and because its rows silently rewrite what
  counts as spending, `etl_writer` has the blanket silver write grant **revoked**
  on it. The vocabulary has two twins by necessity — this table is what gold's
  SQL views read, the `asset_categories` list in `categories.yaml` is what the
  Python side reads (`Categorizer`, and through it `/meta` and `/recurring`) —
  and `tests/test_categorize.py` parses the seed out of the migration to assert
  the two sets are identical, so they cannot drift. A code in the list but not in
  `categories` fails the service at startup rather than quietly mis-counting.
- `trades` — at most one row per movement (PK is the `natural_key`, cascading),
  carrying quantity/side/ISIN/instrument/unit price when the source discloses
  the instrument. `quantity` is **signed** (positive acquiring, negative
  disposing) so a position is a running sum with no side-aware arithmetic.
- `balances` — the balances the statements themselves declare
  (Revolut/Trade Republic per-row running balance → end-of-day anchors; Intesa
  quarterly opening/closing lines), upserted on `(account, balance_date)`.

Balance anchors carry the weight of the completeness check, so they deserve the
detail. Each anchor declares its `basis` — which of the two dates the source's balances
follow (`booking` for Intesa, whose statements total by data contabile;
`value` where the dates coincide) — and `gold.v_reconciliation` sums the
movements by that date, so a valuta crossing a quarter boundary is not a
discrepancy. A non-zero discrepancy therefore localizes genuinely
lost/invented rows to one account and date range. Revolut note: the statement's `Fees` column is informational — the
fee is already inside `Money in/out` (the balance chain proves it), so no
separate fee transaction is ever emitted.

Migrations: Alembic (`src/cashato/db/migrations`).

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
| `category`           | str        | language-neutral **code** (labels live in `categories.yaml`); **NOT NULL** — a NULL would silently vanish from every spend view, because those exclude the asset categories with `NOT IN` and `NULL NOT IN (…)` is NULL, not true. "Nobody knew" is spelled `other` with `category_source='default'` |
| `category_source`    | str        | `mcc` \| `model` \| `rule` \| `manual` \| `default` |
| `category_confidence`| real       | provenance/confidence of the category |
| `native_category`    | str \| null| provider's own category — bootstrap-only, **never** used at runtime |
| `mcc`                | str \| null| ISO 18245 merchant category code when available |
| `transfer_group`     | str \| null| shared id of the two legs of an internal transfer |
| `natural_key`        | str UNIQUE | canonical dedup key |
| `merchant`           | str \| null| counterparty dug out of the description (`parsers/merchant.py`); follows the description's convergence |
| `purchase_time`      | time \| null| time of day the statement text carries (POS/ATM) |

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
(`src/cashato/parsers/detect.py`, no filename guessing) with an optional explicit
`source` override at upload. Inspect a real file before writing/adjusting a parser
— never guess a PDF layout.

- **Revolut** — consolidated-statement **CSV** *and* **PDF**. Sections per
  currency (`Date, Description, Category, Money in/out, Balance …`), amounts
  with `€`/thousands, no `State` column. **EUR only** (other currencies
  ignored). Crypto/savings-interest sections → dedicated accounts and
  `crypto`/`investments` categories.
- **Trade Republic** — statement **PDF** *and* transaction-export **CSV**.
  Position-aware PDF parsing. Deposits/withdrawals/card payments = cash flow;
  securities/dividends → `investments`.
- **Intesa Sanpaolo** — quarterly statement **PDFs** *and* a 13-month
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
2. **Embedding model** — `EmbeddingKNN` (`src/cashato/ml/model.py`): multilingual
   sentence-transformers + kNN; used if `confidence ≥ threshold`. Feature text
   (`build_text`) = the extracted **merchant** when the description carries one
   (in a POS line the counterparty drowns in boilerplate), the full
   `normalize_desc` otherwise (for transfers/salaries the wording IS the
   signal). The default code is never a training class — an example labeled
   `other` is "nobody knew", and as kNN anchors those flooded the sum-vote and
   outvoted exact real-class matches. This does the bulk of the work.
3. **Rules** — thin bilingual keyword safety net (`config/categories.yaml`).
4. Default `other`.

`category` is a language-neutral **code**; per-language labels live in
`config/categories.yaml` (add a language = add a key, no code change). Native
categories are at most an opt-in training-bootstrap signal, off by default.

**ML flow (offline, local):** Ollama (host, GPU) labels the long tail →
`gold.training_labels` → `EmbeddingKNN` trained → recategorize. Ollama is
**not** in-cluster (labeling-time only). On the platform: MLflow (registry,
+ MinIO) + KServe (serving); a separate **categorizer** service does model
categorization off an event, keeping the etl-worker light.

## Internal transfers

`src/cashato/transfers.py` pairs opposite-amount legs on different own-accounts within a
window (`transfers.window_days`), guarded by same-day OR a transfer hint; tags
both legs with `transfer_group`. Gold spend views **exclude** these (they net to
zero, not spending). The etl-worker relinks after every ingest that inserts rows.

---

## Services & API

FastAPI microservices; NATS JetStream backbone. Probes at root (`/healthz`,
`/readyz`), business API under `/api/v1`, `ROOT_PATH` for the gateway, OpenAPI at
`/docs`. Structured JSON logging + Prometheus metrics (`src/cashato/obs.py`).
Metrics are **not** a path on the business API: `Instrumentator().instrument(app)`
is called without `.expose(app)`, and `start_metrics_server` serves the registry
on its own port (`METRICS_PORT`, default **9100**) — uniform across every
service, including the workers that have no HTTP API to hang a path off.

- **ingest-api** — `POST /uploads` (stores file, validates extension → 415 and
  per-file size cap → 413, enqueues a NATS job); `GET /files` (status +
  `rows_new`/`rows_duplicate`/`error`); `POST /feedback` (category correction →
  NATS event; a **write**, so it lives here, not in the read-only query-api);
  `GET /profile` (account holder → home greeting); `POST /admin/reprocess`
  (re-enqueue every stored file, idempotent via `natural_key`) and
  `POST /admin/reset` (`scope=data|all`, destructive);
  `PATCH /admin/accounts/{account_id}` (set or clear an account's display name —
  only the override moves, what the statements disclosed stays as evidence, and
  the id itself is never editable because it is hashed into `natural_key`). It
  sits under `/admin` rather than `/accounts` because the gateway splits the two
  services by path: `GET /api/v1/accounts` belongs to query-api, so a PATCH
  there would need method-based routing to disambiguate. Bronze reads and writes
  both live here because query-api is gold-only by design (and by DB role).
- **etl-worker** — consumes `ingest.jobs` (detect → parse → normalize → dedup →
  persist + fast-path category) and `category.feedback` (apply correction to
  silver + record in `gold.category_feedback`), and **publishes**
  `category.recategorize` after an ingest that landed rows — the third subject on
  the stream, consumed by the categorizer, which runs the model over the new rows
  so the worker itself stays light (no torch/model).
- **query-api** — read-only over gold: `GET /summary`, `/monthly`,
  `/categories/monthly`, `/transactions` (filterable/paginated, with
  filtered-set totals), `/transactions/{natural_key}` (one movement in full —
  how its category was assigned and how confident, the raw provider signals, the
  originating file, the instrument if it was a trade, and the paired leg if it is
  half of an internal transfer; 404 on an unknown key),
  `/meta` (sources from the adapter registry, categories and labels from
  `categories.yaml`, upload limits from `settings.yaml` — one place for
  everything a client needs to build its selectors, so a client reading it can
  never be out of step with what the pipeline accepts),
  `/investments` (positions from `v_holdings` + contributions per month from
  `v_investment_month`; the split between instruments we know and `into_unknown`
  is reported rather than hidden, since a plain transfer to an outside broker
  never discloses its contents. No market prices: `last_price` is the last price
  a statement printed, so `value_at_last_price` is a cost-basis-era figure, not
  today's worth), `/transfers`, `/accounts` (bank/product/joint, composed
  display name), `/reconciliation` (parsed movements vs statement-declared
  balances, `?mismatched_only=true`), `/wealth` (declared balances carried
  forward per month + latest per account, with per-figure `as_of` freshness),
  `/recurring` (subscriptions/salary/rent/bills detected on the fly by
  `src/cashato/recurrence.py` — same merchant key at a steady cadence, with a
  twin-format merge pass; transfers excluded, asset categories listed but kept
  out of the spend totals), `/coverage` (`src/cashato/coverage.py` — per-SOURCE
  staleness scaled to anchor cadence + holes in the union of covered days;
  holes are hints, a missing statement and a quiet period look identical),
  `/merchants` (top merchants by net spend, case-insensitive grouping, refunds
  netted; extraction is per-source in `src/cashato/parsers/merchant.py` —
  transfers/P2P/securities yield no merchant by design). `?lang=it|en` for category labels. The gateway routes ALL of
  `/api/v1` here and enumerates only ingest-api's write paths — enumerating
  both would let a forgotten endpoint fall through to the SPA and answer 200
  with HTML instead of 404.

---

## Extensibility, config, stack

- **Add a source** (fork/monorepo model, no plugin machinery): drop in a
  `src/cashato/parsers/<name>.py` exposing `parse(path) -> list[Transaction]` +
  `DETECTION: list[list[str]]` (content-detection marker groups) + `CURRENCY`,
  and optionally `extract_holder(path)` + `NAME_ORDER` (account holder off the
  document header; `base.addressee_from_words` does the work),
  `extract_accounts(path)` (bank, product, joint/individual, IBAN) and
  `extract_balances(path)` (statement-declared balance anchors feeding
  reconciliation). The registry
  (`registry.py`) auto-discovers it by scanning the package (module name ==
  source id). No config entry needed — detection is parser-coupled, so it lives
  with the parser. See CONTRIBUTING.
- **Parametrized** (runtime `config/*.yaml`, mounted as the `cashato-config`
  ConfigMap — NOT baked into images, loaded via `CASHATO_CONFIG_DIR`):
  `config/settings.yaml` (thresholds, embed model, transfer window, upload caps),
  `config/categories.yaml`, `config/mcc.yaml`, `config/banks.yaml` (ABI -> bank
  name; most statements never name their own bank but all carry an IBAN).
  Editing one deploys via Argo with no image rebuild (stable ConfigMap name, no
  kustomize hash: running pods need a `rollout restart` to pick it up). Infra endpoints/secrets
  stay env/Secret. (There is no `sources.yaml` — the source registry is code,
  see "Add a source".)
- **Stack**: Python 3.12, Postgres 17, SQLAlchemy + psycopg + Alembic,
  pdfplumber, pandas, sentence-transformers (CPU), NATS, FastAPI, ruff + mypy +
  pytest, MIT license. Bootstrap images in `docker/`. Everything in English
  (Italian only in string literals that must match real document text).
- **Three rungs to run it**, same code and same `config/*.yaml`, not variants:
  (1) the **CLI** over a local Postgres — `cli/load.py` imports neither NATS nor
  MinIO, so `cashato-load`/`cashato-export` need nothing else; (2) **`compose.yaml`**
  — Postgres/NATS/MinIO + the two APIs + worker + SPA on a laptop, no cluster;
  (3) the **full platform** on kind (`infra/` OpenTofu + `k8s/` GitOps via Argo).
  Every endpoint is an env var with a localhost default, which is what makes the
  rungs the same build. The model is optional at all three (`if self.model is not
  None`): the chain degrades to MCC → rules → `other`.
  Compose diverges deliberately in two places, both stated at the top of the
  file: one Postgres role instead of the least-privilege split (the baseline
  guards every GRANT with `IF EXISTS` on the role, so it skips them), and no
  `categorizer` (it serves the model through KServe). The SPA image ships **no**
  API proxy because the gateway splits `/api/v1` upstream; `nginx.conf` carries a
  wildcard `include` that matches nothing in-cluster, and compose mounts
  `frontend/api-proxy.conf` into it to stand in for the gateway — its path split
  mirrors `httproutes.yaml`, so both environments route identically.

## Key decisions

- Medallion = schemas in one Postgres. Dedup by canonical key (description
  excluded). Income/expense by sign. Provider-agnostic categorization.
- CI/CD: **Tekton + Argo CD**. On every push to `main` a Gitea webhook drives an
  in-cluster Tekton pipeline: lint/type/test → buildah build+push `svc`+`migrate`
  +`frontend` to **Gitea's built-in OCI registry** tagged by commit SHA → a
  `bump-deploy` step pins those tags in a **separate `cashato-deploy` config
  repo** (Argo watches it), so the build deploys automatically. The **source
  repo stays human-only** (no CI commits); a CEL path-filter only builds on
  `src/**`/`frontend/**`/`docker/**`/`pyproject.toml`/`alembic.ini` changes
  (pushes >50 commits build unconditionally — Gitea truncates the payload the
  filter reads). Model registry = **MLflow**. Secrets: **Sealed Secrets**.
- Platform: kind + Cilium + CNPG + Envoy Gateway + NATS, all IaC (OpenTofu),
  DB roles least-privilege.
- DB backup: CNPG WAL archiving + daily `ScheduledBackup` (13:00 — the
  workstation is off at night; `immediate: true`) to MinIO bucket
  `cnpg-backups`, retention 30d. Protects the only non-re-derivable data:
  manual corrections, `training_labels`, `category_feedback` (files reprocess,
  labels don't). Observability = **LGTM** (Loki/Grafana/Tempo/
  **Mimir**, backends on MinIO S3; collector **Grafana Alloy**): metrics + logs
  + OTel cross-service traces (context propagated through NATS). metrics-server
  added (HPA/`kubectl top`).
- Account **ids** are immutable: they are hashed into `natural_key`. Everything a
  statement says about an account (bank, product, joint) is display metadata in
  `silver.accounts`, projected as `gold.v_accounts`, with a user override on top.
- DB Jobs (`db-migrate`, `db-grants`) are tracked resources with
  `Replace=true`, NOT Argo Sync hooks: a Job's pod template is immutable so plain
  apply can never update it, but hooks are excluded from Argo's diff, so as hooks
  a new image would produce no drift and the migration would silently never run.
- **No personal data in repo files** — examples use `MARIO ROSSI` and fake IBANs;
  the real name belongs only in LICENSE/pyproject authors.
- Multi-user/household is future work (RLS + OIDC), not yet built.
