# cashato — bank transaction data platform

Normalizes transactions from multiple banks (Revolut, Trade Republic, Intesa
Sanpaolo), in heterogeneous formats (CSV/PDF/XLSX), into **one common schema**,
**deduplicates** them across formats/sources, detects **internal transfers**
between your own accounts, and **categorizes** them in a provider-agnostic,
multilingual (IT/EN) way. All processing is **local** — no bank data leaves the
machine.

## Prerequisites

| Tool | Use | Required |
|------|-----|:--------:|
| **Docker** | local Postgres (data core); kind (full platform) | yes |
| **Python 3.12** | parsers, loader, services, ML | yes |
| **Ollama** | offline LLM labeling | ML only |

## Quick start

```bash
python3 -m venv .venv
./.venv/bin/pip install -e '.[svc,dev]'              # installs the cashato package + deps
docker run -d --name cashato-pg -p 5432:5432 \
  -e POSTGRES_USER=cashato -e POSTGRES_PASSWORD=cashato -e POSTGRES_DB=cashato \
  postgres:17-alpine                                 # local Postgres for the data core
./.venv/bin/alembic upgrade head                     # schemas bronze/silver/gold
```

The DB URL is configurable via `DATABASE_URL` (default
`postgresql+psycopg://cashato:cashato@localhost:5432/cashato`).

## Data ingestion

Put files under `data/<source>/` (`data/` is git-ignored for privacy). Each
source accepts **multiple formats**; the source is detected by **content** (each
parser module declares its own `DETECTION` markers), no filename guessing.

| Source | Supported formats |
|--------|-------------------|
| Revolut | consolidated CSV · PDF statement |
| Trade Republic | PDF statement · CSV transaction export |
| Intesa Sanpaolo | 21 quarterly PDF statements · 13-month PDF/XLSX export |

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
no code change). Resolver chain (order = priority):

1. **MCC** (`config/mcc.yaml`, ISO 18245) — when the source exposes the code;
2. **ML model** (embedding kNN, if trained) above a confidence threshold;
3. **Rules** (bilingual regex, `config/categories.yaml`) — thin safety net;
4. `other` fallback.

> Open-source choice: we do **not** depend on providers' native categories
> (taxonomies differ/are inconsistent/often absent). Canonical labels are ours
> (rules + local-LLM labeling + user corrections).

## Internal transfers

Money moved between your own accounts creates two legs (−X on account A, +X on
account B) that are **not spending**. `link_transfers.py` detects the pairs
(equal opposite amount, different account, close dates, with a same-day/hint
guard) and tags both legs with a shared `transfer_group`; the GOLD spend views
exclude them.

```bash
./.venv/bin/cashato-link-transfers        # run after loading
```

## Unified export

```bash
./.venv/bin/cashato-export --lang it   # -> output/transazioni.csv
./.venv/bin/cashato-export --lang en --out output/transactions_en.csv
```

## Services

`ingest-api` (upload → NATS) → `etl-worker` (detect → parse → persist) →
`query-api` (spend aggregates) + `categorizer` (ML categorization off an event).
OpenAPI at `/docs` · `/redoc` · `/openapi.json`; probes at `/healthz` · `/readyz`;
business API under `/api/v1`.

The full stack runs on the local **kind** cluster (IaC) — see `infra/`
(OpenTofu) and `k8s/` (GitOps via Argo CD). CI/CD is **Tekton + Argo CD**: a push
to `main` lints/tests, builds+pushes SHA-tagged images to Gitea's registry, and a
separate `cashato-deploy` config repo pins the tags so Argo auto-deploys the build.
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

### ⚠️ External / manual steps (tracked for reproducibility)

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

**2. Pull the model**: `ollama pull qwen2.5:3b` — **3. Verify**: `curl http://localhost:11434/api/tags`.

> Ollama runs **locally**; no data leaves the machine.

### Run the ML pipeline

```bash
./.venv/bin/python -m cashato.ml.label_llm --model qwen2.5:3b --limit 1000 # label the long tail
./.venv/bin/python -m cashato.ml.train --include-rules --stamp "$(date +%Y%m%d-%H%M)" # train the embedding kNN
./.venv/bin/python -m cashato.ml.recategorize                                # apply + measure `other` drop
```

Metrics are tracked with **MLflow** if installed, otherwise the step is skipped.

## Development

```bash
make install-dev        # runtime + ruff/mypy/pytest/pre-commit
make lint && make test  # ruff + unit tests (no DB/data needed)
```

Verification scripts reconcile each adapter against the statement's declared
totals: `tests/verify_{revolut,trade_republic,intesa}.py`.

## Repository layout

```
src/cashato/      the installable package (pip install -e .)
  config.py (settings loader) · obs.py (logs/metrics/traces) · messaging.py (NATS) · objstore.py (MinIO) · transfers.py
  parsers/        base.py (Transaction, Decimal, dedup) · registry.py (auto-discovered adapters)
                  revolut.py · trade_republic.py · intesa.py (each: parse + DETECTION) · detect.py · categorize.py
  ml/             label_llm.py · train.py · model.py (EmbeddingKNN) · recategorize.py · predictor.py
  db/             db.py (engine) · migrations/ (Alembic)
  services/       ingest_api · etl_worker · query_api · categorizer   (launched via python -m / uvicorn)
  cli/            load.py · export.py · link_transfers.py   (console scripts: cashato-load / -export / -link-transfers)
config/           settings.yaml · categories.yaml · mcc.yaml   (runtime `cashato-config` ConfigMap; not baked)
pyproject.toml    package metadata + deps (base + svc/migrate/train/predict/dev extras)
docker/           Dockerfile.{svc,migrate,frontend,train,predict,mlflow}
infra/            OpenTofu (kind + operators)   k8s/   GitOps manifests (Argo CD)
scripts/          secret-zero.sh · seal-secrets.sh · build-images.sh    tests/  unit + verification
data/  output/  models/   (git-ignored)
```

## License

MIT — see `LICENSE`. Contributions welcome, see `CONTRIBUTING.md`.
