# cashato — codebase review (2026-07-25)

A four-part parallel review (data core, services + security, frontend, platform/
CI). Every finding below was **re-verified against the current working tree**
after review (the detection-collision fix landed mid-review and is already
excluded; two other findings were confirmed already-fixed and dropped). Two bugs
were reproduced empirically — noted inline.

Ordering is by severity. Each item is self-contained: file:line, why it's wrong,
the concrete failure, and a suggested fix. `Confidence` reflects how sure the
finding is; verify before acting on anything marked medium/low.

Reproduce the review context: `.venv/bin/pytest` (41 pass), `.venv/bin/ruff
check`, `.venv/bin/mypy`.

---

## Critical / High

### 1. [HIGH] Intesa parser silently drops whole transactions whose text contains common banking words
`src/cashato/parsers/intesa.py:66` (regex), `:234` (use)

`_SKIP_RE = re.compile(r"saldo|pagina|estratto|totale|segue|riporto|dettaglio|coordinate|iban", re.IGNORECASE)`
is tested with unanchored substring matching on the **whole joined row**, and it
runs **before** the date/amount check. Any real movement whose description
contains one of those words is discarded as if it were a header/balance row.

- **Failure (reproduced):** a quarterly PDF with `PAGAMENTO ESTRATTO CONTO CARTA
  NEXI -350,00` (the standard Italian text for a credit-card settlement debit) —
  the row is silently dropped; a bonifico whose continuation line carries
  `IBAN IT60…` loses that line (truncated description → lost transfer hint →
  worse categorization). No error, no counter: money vanishes from silver.
- **Fix:** apply `_SKIP_RE` only to rows with no date+amount (check
  `_row_dates`/`_row_amount` first), and/or anchor the patterns
  (`^saldo iniziale`, `^totale`, `pagina \d`).
- Confidence: high.

### 2. [HIGH] Revolut crypto-sale amounts ≥ €1,000 are corrupted to a single digit
`src/cashato/parsers/revolut.py:454`

`_crypto_sale_value` does `cell.split(",")` to separate the sale leg from the
purchase leg — but Revolut amounts use comma as the **thousands separator**, so
the split slices inside the number.

- **Failure (reproduced):** `_crypto_sale_value("+ €1,150.00, - €1,000.00")`
  returns `Decimal("1")` — a €1,150 sale stored as €1.00, with a wrong
  `natural_key` (so a corrected re-import won't even dedup against it). Passes in
  the demo only because every synthetic value is < €1,000.
- **Fix:** split on the inter-amount boundary (`re.split(r",\s+(?=[+-])", cell)`)
  or regex-extract `[+-]\s*€[\d,]+\.\d{2}` tokens.
- Confidence: high.

### 3. [HIGH] Two offline/async paths overwrite manual category corrections
`src/cashato/ml/recategorize.py:43,49` **and** `src/cashato/services/categorizer/worker.py` (`_recategorize` UPDATE)

Two separate code paths re-resolve categories without excluding
`category_source='manual'`:
- `ml/recategorize.py` SELECTs **every** row (`SELECT id, description, source,
  mcc FROM silver.transactions`) and UPDATEs each unconditionally — the in-cluster
  worker's own docstring promises `manual` rows are never touched; this offline
  tool breaks that contract.
- the **categorizer worker** filters `category_source IN ('rule','default')` in
  its SELECT but then UPDATEs `WHERE id = :id` with no guard, so a `POST
  /feedback` that flips a row to `manual` *after* the worker's SELECT but before
  its UPDATE (the whole model-inference window) gets clobbered back.

- **Failure:** user corrects a category (survives in `gold.category_feedback`
  but the UI shows silver reverted) after retraining+`recategorize`, or during
  any in-flight recategorize run.
- **Fix:** `recategorize.py` → add `WHERE category_source NOT IN ('manual')` to
  the SELECT. Worker → add `AND category_source IN ('rule','default')` to the
  UPDATE (and consider doing the model HTTP call outside the DB transaction).
- Confidence: high (worker race window grows with the number of `other` rows).

### 4. [HIGH] Destructive admin API is unauthenticated and routed north-south through the gateway
`src/cashato/services/ingest_api/app.py:411` (`reprocess`), `:438` (`reset`); `k8s/manifests/services/base/httproutes.yaml:29`; `infra/01-cluster.tf` (host ports 80/443)

`POST /admin/reset {"scope":"all"}` runs `TRUNCATE … RESTART IDENTITY CASCADE`
over all data + the active-learning memory, then `objstore.clear()` deletes every
retained statement — with **no auth of any kind** (grep: zero `Depends`/API-key/
`Authorization` in the app). The gateway listener is plain HTTP on host port 80
with no `hostname:`, so it answers for any `Host` header.

- **What protects it today:** kind-on-WSL2 binds localhost, single user, no CORS
  granted, FastAPI won't parse JSON from a cross-site form. **But**
  `POST /admin/reprocess` takes no body → a plain cross-site `<form>` submit
  triggers it; and the missing `hostname:` means DNS-rebinding defeats the
  same-origin assumption and can POST `application/json` freely. Read endpoints
  (`/profile`, `/accounts` IBANs) leak PII the same way.
- **Fix (before any exposure past localhost):** a static bearer token
  (SealedSecret) checked by a FastAPI dependency on `/admin/*` (ideally all
  writes); add `hostname:` to the Gateway listener (kills rebinding); add a
  `confirm: "cashato"` field to reset. Alternatively drop `/api/v1/admin` from
  the HTTPRoute and reach it only via `kubectl port-forward`.
- Confidence: high (exposure radius depends on WSL2 networking; the auth gap is
  unconditional).

### 5. [HIGH→MEDIUM] etl-worker acks failed jobs on a WorkQueue stream — a transient failure permanently loses the ingest
`src/cashato/services/etl_worker/worker.py:131-134` (ack in `finally`), `:137-150` (`_handle_ingest` swallows all), `src/cashato/messaging.py:57` (`RetentionPolicy.WORK_QUEUE`)

`_handle_ingest` catches every exception and returns 0; `_consume` acks in a
`finally`; WorkQueue **deletes the message on ack**. There is no `nak`, no
`max_deliver`, no DLQ.

- **Failure:** (a) a one-second MinIO/Postgres blip while processing → job acked
  and gone; the upload is never parsed and only a manual `/admin/reprocess`
  recovers it. (b) A 10 MB PDF that OOMs the 512Mi pod dies before ack → with no
  `max_deliver` the durable consumer redelivers and crash-loops until `max_age`
  (7 d) reaps it. (c) A malformed message makes `json.loads` raise *outside* the
  try → crashes `main()`.
- **Fix:** on failure `await m.nak(delay=…)` instead of ack; create the consumer
  with explicit `ConsumerConfig(ack_wait=…, max_deliver=N)` and `m.term()`/DLQ on
  final failure; move `json.loads` inside the try.
- Confidence: high (severity depends on how often infra blips; the data-loss
  mechanism is certain).

### 6. [HIGH→MEDIUM] Account rename can never be reset — query-api drops `display_name_override` from the response contract
`src/cashato/services/query_api/app.py:148-166` (`Account` model) vs `frontend/src/pages/Manage.tsx:139,167`, `frontend/src/api/types.ts:109`, migration `0013` (`gold.v_accounts` returns the column)

`gold.v_accounts` and the frontend `Account` type both carry
`display_name_override`, but the query-api Pydantic `Account` model does **not**
declare it — so `response_model` silently strips it and the client always sees
`undefined`. Manage gates the Reset button on `!a.display_name_override` and
pre-fills the input from it.

- **Failure:** after renaming an account, reload → Reset button permanently
  disabled, input never shows the current override; it can only be cleared via
  curl.
- **Fix:** add `display_name_override: str | None = None` to the query-api
  `Account` model (frontend already handles it).
- Confidence: high (verified view → model → response filtering → UI gating).

---

## Medium

### 7. Money crosses the API boundary as `float`, violating the project's own "Decimal, never float" rule
`src/cashato/services/query_api/app.py:107-129` (models declare `float`), `:458-465,471-472` (explicit `float()` accumulation in `investments`), `:599` (`float(sum(...))`)

The investments roll-up converts Decimal→float **before** summing over months
(`k["net_invested"] += float(m["net_invested"] or 0)`; `sum(float(...) for m in
months)`). Aggregates arrive exact from the gold views and are then degraded.

- **Failure:** binary-float addition of per-month cents → `total_contributed`
  can disagree with `into_known + into_unknown` recomputed client-side and with
  the DB's exact `sum(amount)` — the exact drift the Decimal rule exists to
  prevent. Magnitude is display-level cents, but it's the stated non-negotiable
  invariant. (Recent commits `ebf32c2`/`0e18440` touched `/investments`; the
  float casts are still present.)
- **Fix:** declare amounts as `Decimal` in the models and do roll-ups in
  `Decimal` (Decimal+Decimal doesn't raise — only mixing with float does); drop
  the `float()` casts.
- Confidence: high on the invariant violation; low on error magnitude.

### 8. Internal-transfer pairing is order-dependent; the feeding query has no ORDER BY → documented idempotency is violated
`src/cashato/cli/link_transfers.py:36` (unordered SELECT), `src/cashato/transfers.py:77-85` (stable-sort tie-break by input order)

`candidates.sort(key=(gap, amount))` breaks ties by list order, which comes from
a `SELECT … FROM silver.transactions` with **no ORDER BY**; the script first runs
`UPDATE … SET transfer_group = NULL`, which can change heap-scan order between
runs.

- **Failure (reproduced):** two identical same-day A→B €500 transfers pair as
  `(1,3),(2,4)` under one row order and `(1,4),(2,3)` under another → different
  `transfer_group` ids across re-runs despite the "idempotent" docstring;
  `/transfers` shows churning identifiers, and in asymmetric ties a different leg
  can end up tagged/untagged.
- **Fix:** `ORDER BY id` on the SELECT; extend the sort key with
  `(o.natural_key, i.natural_key)`.
- Confidence: high mechanism; impact limited to ties (hence medium).

### 9. Upload size cap is enforced only after the whole body is received and spooled to disk
`src/cashato/services/ingest_api/app.py:225-253`

The docstring claims streaming, but `file: UploadFile` + `source: Form(...)`
makes Starlette parse the **entire multipart body** into a SpooledTemporaryFile
(spills to disk at ~1 MB) before the handler runs. The `size > _MAX_FILE_BYTES`
check only bounds the copy to MinIO.

- **Failure:** a 50 GB POST is fully consumed off the network and written to the
  pod's ephemeral storage before the 413 — node-disk exhaustion / ephemeral-
  storage eviction. Memory is fine; disk and bandwidth are not.
- **Fix:** reject early on `Content-Length > cap`; set an Envoy
  `ClientTrafficPolicy` body-size limit at the gateway; keep the in-handler check
  as the authoritative cap.
- Confidence: high mechanism; medium impact (localhost-only today).

### 10. NATS fetch errors are swallowed as "no message" — a broken consumer is an invisible outage
`src/cashato/services/etl_worker/worker.py:124-127`, same pattern in `categorizer/worker.py`

`except Exception: return  # no message` eats **everything**, not just timeouts:
a durable-consumer config conflict after a stream recreate, a deleted stream, a
permissions error — all silent, no log, no metric. The liveness probe is a TCP
check on the metrics port (separate thread), so the worker looks healthy while
uploads queue and expire.

- **Fix:** catch `nats.errors.TimeoutError` specifically for "no message"; log +
  count everything else and let repeated hard errors crash the process so the
  liveness restart helps. (Also: `categorizer/worker.py` wraps the recategorize
  publish *and its log* in `contextlib.suppress(Exception)` — move the log out.)
- Confidence: high.

### 11. Timezone off-by-one when building date filters (`toISOString` on local-midnight dates)
`frontend/src/pages/Dashboard.tsx:21-24,122`, `frontend/src/pages/Transactions.tsx:101-114`, related `frontend/src/lib/format.ts:34`

`endOfMonth` builds a local-midnight Date and formats via
`.toISOString().slice(0,10)`; in Europe/Rome, June 30 00:00 CEST → `2026-06-29`.

- **Failure (verified for Europe/Rome, the deployment TZ):** clicking a heatmap
  cell drills into `date_to=2026-06-29`, silently excluding the last day of the
  month the cell itself counted; YTD includes Dec 31 of the previous year.
- **Fix:** format from local components (`` `${y}-${m}-${d}` `` or
  `toLocaleDateString("en-CA")`), never `toISOString` on a local Date.
- Confidence: high.

### 12. Module-level accounts cache is never invalidated — stale names, and errors cached for the session
`frontend/src/lib/accounts.ts:15-24`

`let cached: Promise<Account[]>` is a module singleton, set on first call, never
cleared; a fetch failure caches `[]` permanently.

- **Failure:** (a) first visit before any upload caches `[]` → pages show raw
  ids (`revolut personal eur`) until a full reload; (b) after `/admin/reset` or a
  rename, every `useAccounts()` page shows old names while Manage (which refetches
  independently) disagrees; (c) one transient startup network error downgrades
  naming for the whole session.
- **Fix:** export `invalidate()` that nulls `cached`, call it after upload/reset/
  rename; don't memoize rejections.
- Confidence: high.

### 13. `index.html` served with no Cache-Control → stale shell → 404 on hashed bundles after a deploy
`frontend/nginx.conf:17-20`

`/assets/` is correctly `immutable, 1y`, but the `location /` serving
`index.html` sends no cache headers → browsers apply heuristic freshness.

- **Failure:** after a new SHA image deploys, a browser that heuristically cached
  the old `index.html` requests `/assets/index-<oldhash>.js` — gone from the new
  image → 404 → blank page until hard refresh.
- **Fix:** `location = /index.html { add_header Cache-Control "no-cache"; }` (and,
  for the "hardened" claim, `X-Content-Type-Options: nosniff`).
- Confidence: medium-high (timing-dependent, well-established mode).

### 14. CI control-plane runs on a mutable, node-local `cashato/migrate:dev` that nothing rebuilds or pins
`k8s/manifests/tekton-ci/base/task-bump-deploy.yaml:27`, `webhook-job.yaml:31`

The `bump-deploy` Task and the Argo Sync-hook webhook Job both hardcode
`image: cashato/migrate:dev`, `imagePullPolicy: IfNotPresent`, with no
`kustomize.images` override — so unlike the migration Job (CI-pinned to `:<sha>`)
they stay `:dev`, which exists only because `scripts/build-images.sh` kind-loaded
it.

- **Failure:** (a) a fresh cluster rebuilt from IaC without the manual build step
  → webhook Job `ErrImagePull` → tekton-ci Sync hook fails → build-on-push dead;
  (b) kubelet image GC evicts the unused-looking `:dev` months later → every
  pipeline fails at `bump-deploy`. Also `apt-get install git` at runtime adds a
  per-run network dependency and needs root.
- **Fix:** bake git into `Dockerfile.migrate` (or a pinned `alpine/git`), and
  reference a registry-qualified pinned tag so containerd pulls it via the
  existing mirror.
- Confidence: high.

### 15. CI path filter can silently skip builds on large pushes; two smaller drift gaps
`k8s/manifests/tekton-ci/base/eventlistener.yaml:41-44`, `docker/Dockerfile.migrate:9`

The CEL filter inspects `body.commits[*]`, which Gitea truncates at
`PAYLOAD_COMMIT_LIMIT` (default 15; not raised in the gitea values). Plus:
`alembic.ini` is COPY'd into the migrate image but isn't in the filter (editing
it never rebuilds the image that ships it); and the filter/CLAUDE.md comment
"only builds on `src/**`/`docker/**`/`pyproject.toml`" omits `frontend/`, which
line 44 does include.

- **Failure:** a batched push (>15 commits, e.g. merging a long branch) whose
  only `src/` change is in a truncated commit is silently not built →
  `cashato-deploy` stays on a stale SHA while same-push manifests deploy → quiet
  code/manifest skew.
- **Fix:** raise `webhook.PAYLOAD_COMMIT_LIMIT` (or also match `body.head_commit`);
  add `alembic.ini` to the CEL list; fix the two comments.
- Confidence: high on the alembic/doc parts; medium on the exact Gitea default.

### 16. All Python backend containers run as root with no pod securityContext
`docker/Dockerfile.{svc,migrate,mlflow,predict,train}` (no `USER`), `k8s/manifests/services/base/{ingest-api,etl-worker,query-api,categorizer}.yaml` (no `securityContext`)

The frontend Deployment already proves the pattern (`runAsNonRoot`,
`readOnlyRootFilesystem`, drop ALL, seccomp). The etl-worker parses **untrusted
uploaded PDFs** (pdfplumber/pdfminer — a historically CVE-bearing surface) as
root with a writable root FS.

- **Fix:** add `USER 1000` to the Python Dockerfiles and mirror the frontend's
  securityContext onto the four service Deployments (they write nothing to root
  FS; uploads stream to MinIO).
- Confidence: high.

---

## Low (correctness latent or ops papercuts)

### 17. `parse_money` drops the minus sign when it isn't leading/trailing
`src/cashato/parsers/base.py:64-67` — `negative` is computed **before** currency
symbols are stripped. **Reproduced:** `parse_money("€-5.00")` → `+5.00`;
`parse_money("(5.00)")` → `+5.00`. Latent (no current format feeds this shape),
but "add a source" is the documented extension path and a bank printing
`EUR -5,00` or accounting parentheses would silently turn expenses into income.
Fix: detect the sign after stripping non-numerics; handle `(...)`. Confidence:
high behavior, low current impact.

### 18. Intesa XLSX lets floats into the money path
`src/cashato/parsers/intesa.py:305` — `Decimal(str(val))` where `val` is an
openpyxl **float**. Round-trips 2-dp amounts in practice, but it's the one place
the "never float" invariant is breached: a >2-dp formula cell goes through float
repr before quantization, and a text-formatted Italian cell (`"1.234,56"`) raises
`InvalidOperation` and fails the whole file. Fix: route string cells through
`parse_money(thousands_sep=".", decimal_sep=",")`. Confidence: high mechanism,
low impact.

### 19. Config is cached/bound at import — mounted-ConfigMap edits never reach running services
`src/cashato/config.py:25-28` (`@cache`), `src/cashato/parsers/categorize.py:32`
(`_DEFAULT_THRESHOLD` at import), `src/cashato/cli/load.py:34` (`_CATEGORIZER` at
import). The design says editing a `config/*.yaml` "deploys via Argo with no image
rebuild", but a long-running worker never re-reads `settings.yaml`; the
categorizer reloads `categorie.yaml` per event yet keeps the stale import-time
threshold. Also `setting()` has no missing-file handling (unlike `bank_names()`),
so a missing `settings.yaml` crashes at import. Fix: bind the threshold at
`Categorizer.load()` time; tolerate a missing file; document that config changes
need a pod restart otherwise. Confidence: high behavior, low severity.

### 20. A 0-row parse *was* recorded as success — now fixed; verify it's complete
`src/cashato/cli/load.py:190` now marks a file `failed` with a reason when a
parse yields zero transactions (fixed alongside the detection work). Worth a test:
confirm a genuinely empty-but-valid statement isn't misreported, and that the
worker surfaces the `failed` status in `/files`. Confidence: fix present,
coverage unverified.

### 21. `/admin/reset` is not atomic across DB, bucket, and job queue
`src/cashato/services/ingest_api/app.py:445-453` — truncate commits, then
`objstore.clear()`; nothing purges in-flight NATS jobs. If MinIO is unreachable
the DB is already wiped while files remain; a reset while a job is mid-`_process`
(file already fetched) re-inserts that statement's rows after the truncate,
attributed to a `file_id` that no longer exists. Fix: clear the bucket first
(retry/verify) then truncate, or purge the `ingest.jobs` consumer as part of
reset; document "run with no ingest in flight". Confidence: medium-high (narrow
timing window).

### 22. Upload `source` override accepted unvalidated; object keys use 32-bit uniqueness
`src/cashato/services/ingest_api/app.py:217-238`, `etl_worker/worker.py:69` — a
typo'd `source=revoult` is silently ignored (falls back to detection) with a 202
and no signal; and `key = f"{uuid4().hex[:8]}_{filename}"` gives 32 bits, and
`fput` overwrites on collision. Fix: 422 on unknown `source`; use the full uuid
hex. Confidence: high behavior, theoretical collision at single-user volume.

### 23. Frontend papercuts
- **`.xls` invited then always rejected:** `frontend/src/pages/Upload.tsx:80`
  `accept=".pdf,.csv,.xlsx,.xls"` vs ingest-api allowlist `{.pdf,.csv,.xlsx}`
  (415). Drop `.xls` or add a parser.
- **Failed-feedback rollback corrupts a null-category select:**
  `frontend/src/pages/Transactions.tsx:126-128` sets `overrides[key] =
  row.category ?? ""` instead of deleting the key (Review.tsx:68 does it right).
- **Column sort sorts only the loaded 50-row page:**
  `frontend/src/pages/Transactions.tsx:84-97` re-sorts one page under a header
  that reads as a global sort → shows the biggest of the 50 newest, not the
  biggest overall. Scope the affordance or add a server sort param.
- Confidence: high.

### 24. Ops papercuts
- **`make db-up`/`db-down` reference a deleted file:** `Makefile:22,25` →
  `deploy/docker-compose.yml`, which doesn't exist (lost in a restructure);
  README uses a raw `docker run`. Make Makefile and README agree.
- **`demo/generate.py` needs `fpdf2` but no extra provides it:** `pyproject.toml`
  `dev` extra lacks it and demo/README's one-liner omits the install. Add
  `fpdf2` to a `dev`/`demo` extra.
- **`gitea-repos.sh:29` hardcodes a fallback admin password**
  (`PW="${PW:-cashato-admin-pw}"`, the old committed default) and embeds it in a
  clone URL. Fail hard if the tfvars is missing; pass creds via netrc/credential
  helper, not the URL.
- **Grafana state is `emptyDir`:** `k8s/manifests/observability/base/grafana.yaml:83`
  — UI-authored dashboards/alerts evaporate on any restart, the one bit of state
  outside git. Provision dashboards as ConfigMaps or give Grafana a PVC +
  `strategy: Recreate`.
- Confidence: high.

---

## Checked and clean (examined, no finding)

- **`natural_key` / dedup / occurrence keys:** stable `sha256`, `ROUND_HALF_UP`
  2dp, `format(amount,"f")`; all 12 demo files across 3 sources parse to exactly
  the 601 expected keys, 0 missing / 0 extra (cross-format + cross-file dedup).
- **SQL injection:** every user value is bound; the only f-string SQL is the
  `TRUNCATE` list keyed off a `Literal["data","all"]` into a hardcoded dict, and
  constant `WHERE` fragments. No injection path.
- **Path traversal:** object keys/filenames never touch local paths (temp files
  via `mkstemp`); only `Path(...).suffix` is filename-derived.
- **Secret hygiene:** no tfstate/tfvars/keys/env git-tracked (`git check-ignore`
  confirms), all secret-bearing Tofu vars `sensitive=true`, every k8s Secret is a
  strict-scoped SealedSecret, nothing sensitive logged.
- **DB least privilege** matches usage (etl_writer scoped to the tables it
  touches incl. the bounded TRUNCATE set; query_reader SELECT-only on gold).
- **db-migrate/db-grants** fix is coherent (tracked, `Replace/Force`, waves 1/2,
  wait-for-DB init, idempotent SQL).
- **Kustomize/Argo:** all 13 overlays build; sync-waves ordered sensibly; no
  `:latest`; resource limits and probes present; RWO/replica handled with
  `strategy: Recreate`; NetworkPolicies default-deny with scoped egress.
- **Webhook security:** HMAC `github` interceptor + branch filter; Gitea
  `ALLOWED_HOST_LIST: private`; least-privilege SA.
- **XSS:** no `dangerouslySetInnerHTML`/`innerHTML`/dynamic `href`; descriptions
  and holder names render through JSX escaping incl. `title` attributes.
- **Frontend API contract** otherwise matches both services exactly (all filter
  params, pagination math, 413/415/422 surfaced, i18n keys cover every
  `category_source`, SPA fallback + `base:"/"` correct).
- **Trade Republic / Revolut cash / detect / registry / categorizer chain /
  load idempotency / migrations 0001–0016 / ML** — see the per-area notes; all
  examined without a defensible finding. `NUMERIC(18,4)` for money throughout;
  gold spend views correctly exclude transfers and investment categories.
- **Tests:** 41 unit tests pass; the three `verify_*.py` E2E scripts are
  correctly excluded via pytest addopts.

---

### Suggested triage order

1. **#1, #2** — silent money loss/corruption in parsers. Fix + add a regression
   test with the offending descriptions/amounts. Cheapest, highest data-integrity
   payoff.
2. **#3** — guard both recategorize paths against `manual`. One-line WHERE each.
3. **#5, #10** — worker durability + visibility (nak/max_deliver, specific
   timeout catch). These convert silent loss into retries + alerts.
4. **#4, #16** — security posture before any exposure past localhost.
5. **#6, #7, #11, #12** — user-visible correctness (reset button, Decimal totals,
   date off-by-one, stale names).
6. Everything else as capacity allows; #20/#24 are quick hygiene wins.

Items #1, #2, #8, #17 have reproducers; the rest were verified by reading the
current code (`file:line` cited). Re-verify any medium/low before large changes —
the tree moves.
