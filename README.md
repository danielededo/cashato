# cashato — bank transaction data platform

![Python](https://img.shields.io/badge/Python_3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL_17-4169E1?logo=postgresql&logoColor=white)
![NATS](https://img.shields.io/badge/NATS_JetStream-27AAE1?logo=natsdotio&logoColor=white)
![React](https://img.shields.io/badge/React-61DAFB?logo=react&logoColor=black)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?logo=typescript&logoColor=white)
![Kubernetes](https://img.shields.io/badge/Kubernetes_(kind)-326CE5?logo=kubernetes&logoColor=white)
![OpenTofu](https://img.shields.io/badge/OpenTofu-FFDA18?logo=opentofu&logoColor=black)
![Argo CD](https://img.shields.io/badge/Argo_CD-EF7B4D?logo=argo&logoColor=white)
![Tekton](https://img.shields.io/badge/Tekton_CI-FD495C?logo=tekton&logoColor=white)
![MLflow](https://img.shields.io/badge/MLflow-0194E2?logo=mlflow&logoColor=white)
![Grafana](https://img.shields.io/badge/LGTM_stack-F46800?logo=grafana&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green)

Normalizes transactions from multiple banks (Revolut, Trade Republic, Intesa
Sanpaolo), in heterogeneous formats (CSV/PDF/XLSX), into **one common schema**,
**deduplicates** them across formats/sources, detects **internal transfers**
between your own accounts, and **categorizes** them in a provider-agnostic,
multilingual (IT/EN) way. All processing is **local** — no bank data leaves the
machine. Money is **`Decimal` end to end**, never `float` — including on the
wire, where amounts travel as JSON strings.

## Why

This project was born from a personal need: a **single place to see all of my
finances** — scattered across banks that don't talk to each other — and to
understand **where my money actually goes** and **what my recurring habits
are** (subscriptions, salary, rent, bills). Not budgeting software, but
**financial awareness**: one dashboard that answers "how am I really doing?"
without handing my statements to a third-party service.

It doubles as a **learning ground**: an excuse to go deep on tools and
open-source infrastructure — Kubernetes, GitOps (Argo CD), in-cluster CI
(Tekton), event streaming (NATS), local ML (embeddings + a local LLM), and the
LGTM observability stack — at a deliberately over-engineered scale for a
one-user app, because that's the point.

## How it flows

```mermaid
flowchart LR
    subgraph client["Browser"]
        SPA["React SPA"]
    end
    subgraph cluster["kind cluster"]
        GW["Envoy Gateway"]
        IN["ingest-api"]
        Q["query-api"]
        W["etl-worker"]
        C["categorizer<br/>(KServe model)"]
        NATS[("NATS<br/>JetStream")]
        OBJ[("MinIO<br/>stored statements")]
        subgraph pg["PostgreSQL (medallion)"]
            B[("bronze<br/>raw files")]
            S[("silver<br/>transactions")]
            G[("gold<br/>views")]
        end
    end
    SPA -->|"upload / feedback"| GW --> IN
    SPA -->|"analytics"| GW --> Q
    IN -->|"store the file"| OBJ
    IN -->|"ingest.jobs"| NATS --> W
    OBJ -->|"fetch to parse"| W
    W -->|"register the file"| B
    W -->|"detect → parse → dedup"| S
    W -->|"recategorize event"| NATS --> C --> S
    S --> G --> Q
```

`ingest-api` only stores the file and enqueues a job; the **etl-worker** is what
writes `bronze.raw_files` and silver, fetching the stored statement back from
MinIO to parse it. That hop is why re-processing needs no raw-row landing table:
the retained file plus its sha256 is the record. `query-api` is **read-only over
gold**, enforced by its DB role rather than by convention.

## Prerequisites

| Tool | Use | Required |
|------|-----|:--------:|
| **Docker** | local Postgres (data core); compose (full app); kind (full platform) | yes |
| **Python 3.12** | parsers, loader, services, ML | CLI only |
| **Ollama** | offline LLM labeling | ML only |

## Three ways to run it

Pick the rung you need — they are the same code and the same `config/*.yaml`,
not variants of it. Nothing forces you up the ladder.

| | What you get | What it costs |
|---|---|---|
| **1. CLI** | statements in, categorized CSV out | a venv + one Postgres container ([quick start](#quick-start)) |
| **2. Compose** | the whole app: upload, dashboard, review, wealth | `docker compose up` ([below](#full-app-on-your-laptop)) |
| **3. Kubernetes** | + GitOps, CI/CD, MLflow/KServe, LGTM observability | kind + OpenTofu ([`infra/`](infra/README.md), [`k8s/`](k8s/README.md)) |

The ML model is optional at every rung: the resolver falls back from MCC codes
to bilingual rules to `other`, so categorization works before you train
anything.

## Quick start

```bash
python3 -m venv .venv
./.venv/bin/pip install -e '.[svc,migrate,dev]'      # installs the cashato package + deps
docker run -d --name cashato-pg -p 5432:5432 \
  -e POSTGRES_USER=cashato -e POSTGRES_PASSWORD=cashato -e POSTGRES_DB=cashato \
  postgres:17-alpine                                 # local Postgres for the data core
./.venv/bin/alembic upgrade head                     # schemas bronze/silver/gold
```

The DB URL is configurable via `DATABASE_URL` (default
`postgresql+psycopg://cashato:cashato@localhost:5432/cashato`).

**No statements at hand?** The repo ships a fully synthetic demo dataset
(persona *Mario Bianchi* — see [`demo/`](demo/README.md)) covering every source
and every format but one — there is no sample of the Intesa 13-month **PDF**
export, only its XLSX twin:

```bash
./.venv/bin/cashato-load --source revolut demo/revolut_consolidated_statement.csv
./.venv/bin/cashato-load --source intesa  demo/intesa_estratto_conto_2025_Q1.pdf
./.venv/bin/cashato-export --lang en --out output/transactions_en.csv
```

## Data ingestion

Put files under `data/<source>/` (`data/` is git-ignored for privacy). Each
source accepts **multiple formats**; the source is detected by **content** (each
parser module declares its own `DETECTION` markers), no filename guessing.

| Source | Supported formats |
|--------|-------------------|
| Revolut | consolidated CSV · PDF statement |
| Trade Republic | PDF statement · CSV transaction export |
| Intesa Sanpaolo | quarterly PDF statements · 13-month PDF/XLSX export |

```bash
./.venv/bin/cashato-load --source revolut        "data/Revolut/consolidated-....csv"
./.venv/bin/cashato-load --source trade_republic "data/trade_republic/Transaction export.csv"
./.venv/bin/cashato-load --source intesa         "data/intesa/....pdf"
```

The loader is **idempotent**: the same file (or overlapping exports, or different
formats of the same source) does not create duplicates. The canonical dedup key
is `hash(account, value_date, amount, occurrence_index)` — format-independent
(the description, which varies across formats, is not part of the key).

## Categorization (provider-agnostic, multilingual)

The stored category is always a language-neutral **code** (e.g. `dining`);
per-language labels live in `config/categories.yaml` (add a language = add a key,
no code change). MCC wins outright when present; between the model and the
keyword rules the order is decided **per row**, by whether a merchant could be
extracted from the description:

```mermaid
flowchart LR
    T["transaction"] --> MCC{"MCC code?<br/>(ISO 18245)"}
    MCC -- "yes" --> A["category<br/>(source: mcc)"]
    MCC -- "no" --> M{"merchant<br/>extracted?"}
    M -- "yes" --> ML{"embedding kNN<br/>confidence ≥ threshold?"}
    ML -- "yes" --> B["category<br/>(source: model)"]
    ML -- "no" --> R2{"keyword rule?"}
    M -- "no" --> R{"keyword rule?"}
    R -- "yes" --> C["category<br/>(source: rule)"]
    R -- "no" --> ML2{"embedding kNN<br/>confidence ≥ threshold?"}
    ML2 -- "yes" --> B
    ML2 -- "no" --> D["other<br/>(source: default)"]
    R2 -- "yes" --> C
    R2 -- "no" --> D
    U["user correction"] -. "always wins, never overwritten" .-> E["category<br/>(source: manual)"]
```

**Why the order flips.** With a merchant the model leads: embeddings generalize
to merchants no rule has ever heard of. Without one the feature text is
operation boilerplate where every wire transfer looks like every other — the
distinguishing word (*"Affitto"*) drowns for the embedding but is exactly what a
keyword rule reads, so rules lead there (`parsers/categorize.py`).

> Design choice: we do **not** depend on providers' native categories
> (taxonomies differ/are inconsistent/often absent). Canonical labels are ours
> (rules + local-LLM labeling + user corrections).

## Internal transfers

Money moved between your own accounts creates two legs (−X on account A, +X on
account B) that are **not spending**. The transfer linker detects the pairs
(equal opposite amount, different account, close dates, with a same-day/hint
guard) and tags both legs with a shared `transfer_group`; the GOLD spend views
exclude them. The etl-worker relinks automatically after every ingest; the CLI
exists for the local data-core workflow:

```bash
./.venv/bin/cashato-link-transfers        # run after loading
```

## Unified export

```bash
./.venv/bin/cashato-export --lang it   # -> output/transazioni.csv
./.venv/bin/cashato-export --lang en --out output/transactions_en.csv
```

## Full app on your laptop

No Kubernetes, no Gitea, no Argo — one command brings up Postgres, NATS, MinIO,
the two APIs, the worker and the SPA:

```bash
docker compose up --build          # then open http://localhost:8080
docker compose down                # add -v to drop the data volumes too
```

Upload statements from the Upload page (or use the synthetic ones in
[`demo/`](demo/README.md)) and the dashboard fills in. The APIs are also
published directly for `curl` and OpenAPI: `http://localhost:8000/docs`
(ingest) and `http://localhost:8001/docs` (query).

It builds the same images the cluster runs — one shared `svc` image behind all
three service containers, plus `migrate` and `frontend` — and every endpoint the
code needs is an environment variable, so nothing is forked or stubbed. Two things
differ on purpose, both documented at the top of `compose.yaml`: a single
Postgres role instead of the least-privilege split CNPG provisions, and no
`categorizer` (it serves the model through KServe; without it the resolver
still falls back to MCC codes and rules).

One detail worth knowing if you touch the frontend: the image ships **no** API
proxy, because in the cluster the Envoy Gateway splits `/api/v1` off before
nginx ever sees it. `compose.yaml` mounts `frontend/api-proxy.conf` into a
wildcard `include` that `nginx.conf` carries, which makes nginx stand in for the
gateway. The wildcard matches nothing in the cluster, so that path is a literal
no-op there — and the proxy's path split mirrors `httproutes.yaml`, so both
environments route identically.

## Services & frontend

`ingest-api` (upload → NATS) → `etl-worker` (detect → parse → persist) →
`query-api` (spend aggregates) + `categorizer` (ML categorization off an event).
OpenAPI at `/docs` · `/redoc` · `/openapi.json`; probes at `/healthz` · `/readyz`;
business API under `/api/v1`.

The **React SPA** (`frontend/`) is the daily driver: dashboard with period
filters, day-grouped transactions with server-side totals, wealth page
(contributions vs known instruments), category review with one-click manual
correction (feeds active learning), file upload, account management, IT/EN,
light/dark, and a privacy mode that blurs every monetary figure.

The full stack runs on the local **kind** cluster (IaC) — see
[`infra/`](infra/README.md) (OpenTofu) and [`k8s/`](k8s/README.md) (GitOps via
Argo CD). CI/CD is **Tekton + Argo CD**: a push to `main` lints/tests,
builds+pushes SHA-tagged images to Gitea's registry, and a separate
`cashato-deploy` config repo pins the tags so Argo auto-deploys the build
(details: [`k8s/manifests/tekton-ci/`](k8s/manifests/tekton-ci/README.md)).
Once deployed, the services are reached through the Envoy Gateway:

```bash
curl -F "file=@data/.../file.csv" http://<gateway-ip>/api/v1/uploads
curl "http://<gateway-ip>/api/v1/summary?lang=en"
```

## ML pipeline (advanced categorization)

Rules cover part of the transactions; the **long tail** (unseen merchants, e.g.
"Metro de Madrid") needs world knowledge. A local **LLM** generates canonical
labels; a lightweight **embedding kNN** classifier (multilingual
sentence-transformers) is trained on them (fast, no LLM at inference).

```mermaid
flowchart LR
    O["Ollama (host, GPU)<br/>labels the long tail"] --> TL[("gold.training_labels")]
    FB[("gold.category_feedback<br/>user corrections")] --> TR
    TL --> TR["train<br/>EmbeddingKNN"]
    TR --> MR["MLflow registry<br/>@champion"]
    MR --> KS["KServe predictor"]
    KS --> CG["categorizer service"]
```

A **registered** retrain promotes the challenger to `@champion` only if it
matches or beats the incumbent's macro-F1 on the holdout, so a bad retrain never
regresses serving (see [`scripts/`](scripts/README.md)). `models/latest.joblib`
— what the batch recategorize applies — follows the same verdict: it is written
when a challenger is promoted and **left untouched when one is rejected**, so a
refused model cannot sneak into the batch path behind the registry's back. An
unregistered run writes it too, deliberately: with no registry to arbitrate,
that run's model *is* the local model.

### External / manual steps (tracked for reproducibility)

**1. Install Ollama** (local LLM):

```bash
# (a) official installer (needs sudo)
curl -fsSL https://ollama.com/install.sh | sh
# (b) userspace (no sudo): GitHub release tarball (zstd)
curl -fSL https://github.com/ollama/ollama/releases/latest/download/ollama-linux-amd64.tar.zst \
  -o ~/.local/ollama.tar.zst
tar --zstd -xf ~/.local/ollama.tar.zst -C ~/.local   # or decompress via `python -m zstandard`
export PATH="$HOME/.local/bin:$PATH" && ollama serve &
```

**2. Pull the model**: `ollama pull qwen2.5:7b` — **3. Verify**: `curl http://localhost:11434/api/tags`.
(`7b` is the code default and the recommendation; `--model`/`OLLAMA_MODEL`
overrides it. A smaller one labels faster and worse, which for the long tail is
the wrong trade.)

> Ollama runs **locally**; no data leaves the machine.

### Run the ML pipeline

The training and recategorize steps need the **`train` extra** — `sklearn`,
`joblib`, `sentence-transformers` — which the quick-start venv does not have:

```bash
./.venv/bin/pip install -e '.[svc,migrate,dev,train]'                      # + the ML deps
./.venv/bin/python -m cashato.ml.label_llm --limit 1000                    # label the long tail
./.venv/bin/python -m cashato.ml.train --include-rules --stamp "$(date +%Y%m%d-%H%M)" # train the embedding kNN
./.venv/bin/python -m cashato.ml.recategorize                              # apply + measure `other` drop
```

**MLflow tracking is opt-in, not automatic.** Without `--register`, `train`
writes `models/latest.joblib` and nothing else: no run is logged, no version is
created, and no promotion gate runs at all. Add `--register` to log the metrics
and register a version; promotion is then governed by `--promote`, whose
`if-better` default keeps the incumbent unless the challenger's macro-F1 is
**greater than or equal to** the champion's — a tie promotes the newer model.
`--promote always` skips the comparison.

## Development

```bash
make install            # package + svc/migrate extras + ruff/mypy/pytest/pre-commit
make lint && make test  # ruff + unit tests (no DB/data needed)
```

Use `make install`, not `make install-dev`: the latter installs the `dev` extra
alone, and the suite imports `fastapi` and `nats` (in `test_csrf_guard.py` and
`test_messaging.py`), which live in `svc`. Both CI paths install `.[svc,dev]`
for the same reason.

Verification scripts reconcile each adapter against the statement's declared
totals: `tests/verify_{revolut,trade_republic,intesa}.py`.

## Repository layout

```
src/cashato/      the installable package (pip install -e .)
  config.py (settings loader) · obs.py (logs/metrics/traces) · messaging.py (NATS) · objstore.py (MinIO) · transfers.py
  recurrence.py (recurring-movement detection) · coverage.py (per-source staleness/holes) · model_client.py (KServe)
  parsers/        base.py (Transaction, Decimal, dedup) · registry.py (auto-discovered adapters)
                  revolut.py · trade_republic.py · intesa.py (each: parse + DETECTION) · detect.py · categorize.py
                  merchant.py (counterparty + time-of-day out of the description)
  ml/             label_llm.py · train.py · model.py (EmbeddingKNN) · recategorize.py · predictor.py
                  registry.py (MLflow @champion) · register_model.py
  db/             db.py (engine) · migrations/ (Alembic)
  services/       ingest_api · etl_worker · query_api · categorizer   (launched via python -m / uvicorn)
  cli/            load.py · export.py · link_transfers.py   (console scripts: cashato-load / -export / -link-transfers)
frontend/         React + Vite + TS SPA, served by nginx behind the gateway
config/           settings.yaml · categories.yaml · mcc.yaml · banks.yaml   (runtime ConfigMap; not baked into images)
docker/           Dockerfile.{svc,migrate,frontend,train,predict,mlflow}
infra/            OpenTofu (kind + operators)      k8s/   GitOps manifests (Argo CD)
demo/             generate.py → synthetic statements (Mario Bianchi) + expected_transactions.csv
                  other_banks/ (files that must stay UNCLAIMED) · DETECTION_COLLISIONS.md
scripts/          secret-zero.sh · seal-secrets.sh · build-images.sh · gitea-repos.sh
tests/            unit tests + manual verification scripts
.github/          Actions (lint/type/test, CodeQL) + Dependabot — the contributor-facing CI
compose.yaml      the whole app on a laptop, no Kubernetes
CLAUDE.md         architecture and conventions in depth
alembic.ini · Makefile · pyproject.toml
data/  output/  models/   (git-ignored: real statements, exports, model artifacts)
```

Each subsystem has its own README:
[`frontend/`](frontend/README.md) · [`config/`](config/README.md) ·
[`docker/`](docker/README.md) · [`infra/`](infra/README.md) ·
[`k8s/`](k8s/README.md) · [`demo/`](demo/README.md) ·
[`scripts/`](scripts/README.md) · [`tests/`](tests/README.md).
Architecture and conventions in depth: [`CLAUDE.md`](CLAUDE.md).

## License

MIT — see `LICENSE`. Contributions welcome, see `CONTRIBUTING.md`.
