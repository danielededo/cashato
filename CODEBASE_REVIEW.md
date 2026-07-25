# cashato — full codebase review (briefing for the implementing agent)

Reviewed at commit `1416c37` (2026-07-25, working tree clean). Method: four
parallel review passes (data core / services+security / frontend / platform+CI),
each instructed to adversarially refute its own findings before reporting; the
top findings were then **re-verified against HEAD line-by-line** because fixes
landed mid-review. Line numbers are correct at this commit and will drift.

Legend: ✅ = re-verified on HEAD by the coordinator; (a) = agent-verified with
code evidence, not independently re-checked.

Already fixed during this review cycle (do NOT redo): Intesa detection-marker
collisions (`demo/DETECTION_COLLISIONS.md`, commit `1416c37`); 0-row parses now
marked `failed` (`cli/load.py:190`); `/investments` Decimal+float 500
(`ebf32c2`) — though finding #9 below remains.

---

## HIGH — data loss / data corruption

### 1. ✅ Intesa quarterly parser silently DROPS transactions whose description contains common banking words
`src/cashato/parsers/intesa.py:66` (`_SKIP_RE`), `:234` (applied before the date/amount check).
`_SKIP_RE = saldo|pagina|estratto|totale|segue|riporto|dettaglio|coordinate|iban` runs on the joined row text BEFORE checking whether the row is a real movement. Empirically reproduced: a movement `PAGAMENTO ESTRATTO CONTO CARTA NEXI` (the standard credit-card settlement debit!) is dropped without trace; a continuation line containing `IBAN IT60...` is truncated out of the description (which also kills the transfer hint). Money silently missing from silver.
**Fix:** apply `_SKIP_RE` only to rows that have no valid date+amount (evaluate `_row_dates`/`_row_amount` first), and/or anchor the patterns (`^saldo`, `^totale`, `pagina \d`). Add a regression test with "ESTRATTO CONTO" inside a movement description.

### 2. ✅ Revolut crypto-sale amounts ≥ €1,000 corrupted to the thousands digit
`src/cashato/parsers/revolut.py:454-459`. `_crypto_sale_value` splits the cell on `","`, but Revolut amounts use comma as thousands separator: `"+ €1,150.00, - €1,000.00"` → `Decimal("1")`. Wrong amount AND wrong `natural_key` (a corrected re-import would not even dedup against the bad row).
**Fix:** `re.split(r",\s+(?=[+-])", cell)` or regex-extract `[+-]\s*€[\d,]+\.\d{2}` tokens. Regression test with a 4-digit sale.

### 3. ✅ `ml/recategorize.py` overwrites MANUAL user corrections
`src/cashato/ml/recategorize.py:43-50`. `SELECT id, description, source, mcc FROM silver.transactions` with **no `category_source` filter**, then unconditional UPDATE. The in-cluster categorizer worker correctly restricts to `('rule','default')`; this documented offline tool (run after every retrain) violates that contract — every `manual` correction reverts, surviving only in `gold.category_feedback` which nothing replays.
**Fix:** add `WHERE category_source IN ('rule','default','model')` to the SELECT (and mirror it in the UPDATE guard, see #7).

### 4. ✅ etl-worker ACKs failed jobs on a WorkQueue stream — transient failure permanently loses the upload
`src/cashato/services/etl_worker/worker.py:118-150`, `src/cashato/messaging.py:54-58` (RetentionPolicy.WORK_QUEUE deletes on ack).
`try: await handler(...) finally: await m.ack()` + `_handle_ingest` swallows every exception and returns 0 → a MinIO/Postgres blip during processing acks (= deletes) the job; the file stays `pending` forever, recoverable only via manual `/admin/reprocess`. Symmetric problems: a malformed message (`json.loads` raises outside the inner try) crashes `main()`; an OOM before ack redelivers with no `max_deliver` → crash-loop; default 30s `ack_wait` can redeliver a legitimately slow parse mid-flight (dangerous under `force=True`).
**Fix:** on handler failure `nak(delay=...)` instead of ack; explicit `ConsumerConfig(ack_wait=…, max_deliver=N)` + `term()` (or DLQ) on final failure; move `json.loads` inside the try.

### 5. ✅ Destructive admin API is unauthenticated and exposed on host ports 80/443
`src/cashato/services/ingest_api/app.py:411` (reprocess), `:438` (reset — `TRUNCATE … CASCADE` + MinIO bucket clear); routed by `k8s/manifests/services/base/httproutes.yaml:29` (`/api/v1/admin` → ingest-api); kind maps 80/443 to the host (`infra/01-cluster.tf:50,55`); the Gateway listener has **no hostname** (`k8s/manifests/gateway/base/gateway.yaml:12-18`) so DNS-rebinding defeats same-origin assumptions; zero auth constructs exist in the app (grep verified). `POST /admin/reprocess` takes no body → even a plain cross-site `<form>` triggers it. Read endpoints leak PII (names, IBANs) the same way.
Protected today ONLY by: WSL2 localhost reachability + single user.
**Fix (cheap, do before any exposure):** static bearer token from a SealedSecret enforced by a FastAPI dependency on `/admin/*` (ideally all writes) + `hostname:` on the Gateway listener + a `confirm` field on reset. Alternative/minimum: remove `/api/v1/admin` from the HTTPRoute and use port-forward for admin.

### 6. ✅ Account rename "Reset" is permanently broken — query-api drops `display_name_override` from the contract
`src/cashato/services/query_api/app.py:148-167`: the `Account` response model does not declare `display_name_override`, so FastAPI's `response_model` filtering silently strips it, though `gold.v_accounts` returns it (migration 0013) and the frontend types declare it. `frontend/src/pages/Manage.tsx:139,167` gates the Reset button on that field → always disabled, input never pre-filled; override clearable only via curl.
**Fix:** add `display_name_override: str | None = None` to the query-api model. Frontend already handles it.

---

## MEDIUM — correctness / robustness

### 7. (a) Categorizer worker can clobber a manual correction (race)
`src/cashato/services/categorizer/worker.py:50-74`. SELECT filters `('rule','default')`, then a long KServe call runs inside the open transaction, then `UPDATE … WHERE id=:id` **without re-checking `category_source`**. A `/feedback` correction landing in that window is overwritten.
**Fix:** `UPDATE … WHERE id=:id AND category_source IN ('rule','default')`; move the model call outside the DB transaction.

### 8. ✅ Internal-transfer pairing is not idempotent (unordered feed + order-dependent tie-break)
`src/cashato/cli/link_transfers.py:36` (SELECT with no ORDER BY — verified), `src/cashato/transfers.py:77-85` (stable sort ties broken by input order). The script NULLs all groups then re-pairs; heap order changes → identical same-day transfer pairs get different `transfer_group` ids across runs, contradicting the docstring's idempotency claim.
**Fix:** `ORDER BY id` in the SELECT + extend the sort key with `(out.natural_key, in.natural_key)`.

### 9. ✅ Money crosses the query-api boundary as float, with float accumulation
`src/cashato/services/query_api/app.py:458-472` (`float()` per-month accumulation for investments), `:599` (`float(sum(...))`), plus `float`-typed amounts in every response model. Violates the project's non-negotiable Decimal rule; totals can drift cents vs. client-side recomputation. (Commit `ebf42c2`/`0e18440` fixed the 500 and headline sums, but the float math remains.)
**Fix:** keep roll-ups in `Decimal`, declare `Decimal` in Pydantic models, convert to JSON number only at the final serialization hop.

### 10. (a) Upload size cap enforced only AFTER the full body is spooled to disk
`src/cashato/services/ingest_api/app.py:225-253`. `UploadFile`+`Form` makes Starlette consume the entire multipart body into a SpooledTemporaryFile before the handler runs; the 413 check happens later. A 50GB POST exhausts pod ephemeral storage. Docstring's "streaming, cannot exhaust memory" claim is about memory only.
**Fix:** early reject on `Content-Length` header + an Envoy request-body size limit (ClientTrafficPolicy) for dishonest clients; keep the in-handler check as authoritative.

### 11. (a) All NATS fetch errors silently treated as "no message" — a broken consumer is an invisible total outage
`src/cashato/services/etl_worker/worker.py:124-127` and `src/cashato/services/categorizer/worker.py:86-89`: `except Exception: return`. A durable-consumer config conflict (a hazard `messaging.py:47-51` itself documents), deleted stream, or permission error is eaten with no log/metric; liveness is a TCP check on the metrics thread, so the pod stays "healthy" while uploads expire (7d max_age). Also `worker.py:182` wraps the recategorize publish AND its log line in `contextlib.suppress`.
**Fix:** catch `nats.errors.TimeoutError` for the empty case; log + `JOBS.labels(status="fetch_error")` for the rest; let repeated hard errors crash the process; move the log out of the suppress.

### 12. (a) CI control-plane runs on a mutable node-local `cashato/migrate:dev` nothing rebuilds
`k8s/manifests/tekton-ci/base/task-bump-deploy.yaml:27` and `webhook-job.yaml:31` hardcode `:dev` (only ever kind-loaded by `scripts/build-images.sh`; the Gitea registry has only `:<sha>`). Fresh cluster from IaC → webhook Job `ErrImagePull` → build-on-push dead; kubelet image GC → `bump-deploy` fails months later. The task also `apt-get install git` at runtime.
**Fix:** bake git into the image, reference a registry-qualified pinned tag pulled via the existing containerd mirror.

### 13. ✅ `make db-up`/`db-down` reference `deploy/docker-compose.yml`, which does not exist
`Makefile:22,25`; `deploy/` is absent from the repo (verified). README documents a raw `docker run` instead.
**Fix:** restore the compose file or point the targets at the README's command.

### 14. (a) CI path filter: webhook commit-list truncation can silently skip builds; `alembic.ini` not filtered; doc drift
`k8s/manifests/tekton-ci/base/eventlistener.yaml:41-44`. Gitea truncates the webhook `commits` array (`PAYLOAD_COMMIT_LIMIT`, default 15, not raised in `infra/modules/gitea/values.yaml`) → a >15-commit push whose only `src/` change is in a truncated commit builds nothing while manifests still deploy (code/manifest skew). `alembic.ini` ships in the migrate image (`docker/Dockerfile.migrate:9`) but is not in the filter. CLAUDE.md and the manifest comment omit `frontend/`, which the filter does include.
**Fix:** raise `PAYLOAD_COMMIT_LIMIT` (or also match `body.head_commit`), add `alembic.ini`, sync the docs.

### 15. (a) Frontend date filters are timezone off-by-one (`toISOString` on local dates)
`frontend/src/pages/Dashboard.tsx:21-24,122`, `frontend/src/pages/Transactions.tsx:101-114`, `frontend/src/lib/format.ts:34`. In Europe/Rome, `endOfMonth` yields `…-29` (excludes the month's last day from drill-downs) and YTD starts at Dec 31 of the previous year.
**Fix:** format from local components (`toLocaleDateString("en-CA")` or manual `${y}-${m}-${d}`); never `toISOString` on a local-midnight Date.

### 16. (a) Frontend accounts cache is a never-invalidated module singleton (and caches failures)
`frontend/src/lib/accounts.ts:15-24`. Stale names after upload/rename/reset until hard reload; a transient startup fetch error downgrades naming for the whole session (`() => []` memoized).
**Fix:** export `invalidate()`; call it after upload success/reset/rename; don't memoize rejections.

### 17. (a) `index.html` served with no Cache-Control → stale shell 404s on hashed bundles after deploys
`frontend/nginx.conf:17-20` (assets immutable ✓, shell heuristic-cached ✗). Classic blank-page-after-deploy.
**Fix:** `location = /index.html { add_header Cache-Control "no-cache"; }` (+ `X-Content-Type-Options: nosniff` while there).

---

## LOW — hardening / polish (grouped)

### 18. (a) `/admin/reset` not atomic across DB, bucket, queue
`ingest_api/app.py:445-453`. DB truncation commits before `objstore.clear()`; nothing purges queued/in-flight NATS jobs — a mid-reset ingest re-inserts rows referencing a truncated `file_id`; a MinIO failure leaves DB wiped but files present.
**Fix:** clear bucket first; purge the `ingest.jobs` consumer during reset.

### 19. (a) Upload `source` override unvalidated; object key uses 32-bit uniqueness prefix
`ingest_api/app.py:217-238` (typo'd source silently falls back to detection with a 202), `etl_worker/worker.py:69`; `uuid4().hex[:8]` + `fput` overwrite-on-collision.
**Fix:** 422 on unknown source; use full uuid hex.

### 20. (a) All Python backend containers run as root with no pod securityContext
`docker/Dockerfile.{svc,migrate,mlflow,predict,train}` (no `USER`), the four service Deployments (no `securityContext`) — while `frontend/base/deployment.yaml:28-40` shows the project already knows the full pattern. etl-worker parses untrusted PDFs (pdfplumber — CVE-bearing surface) as root.
**Fix:** `USER 1000` in the Dockerfiles + mirror the frontend's securityContext.

### 21. ✅(behavior) `parse_money` sign traps: `"€-5.00"` → **+5.00**, `"(5.00)"` → +5.00
`src/cashato/parsers/base.py:64-67` (sign detected before symbol stripping). Latent — no current format feeds these shapes — but this is the documented extension point for new sources; a sign flip also corrupts `natural_key`.
**Fix:** detect the sign after stripping, or on `re.search(r"-|\(.*\)")` of the raw string.

### 22. (a) Intesa XLSX amounts pass through float
`src/cashato/parsers/intesa.py:305` `Decimal(str(val))` where openpyxl yields float. Safe for 2-decimal cells via shortest-repr, but it is the one breach of the Decimal invariant in the parsers; a text-formatted Italian cell raises and fails the file.
**Fix:** string cells → `parse_money(…