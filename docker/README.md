# docker/ — container images

Six images, split by weight: the light ones are CI-built on every push, the
heavy ML ones are built manually (torch is too slow for buildah-on-kind).

| Image | Dockerfile | Runs | Built by |
|-------|-----------|------|----------|
| `cashato/svc` | `Dockerfile.svc` | ingest-api, etl-worker, query-api, categorizer (one image, command set by k8s) | **CI** on push |
| `cashato/migrate` | `Dockerfile.migrate` | Alembic migration Job + grants Job; also the CI `bump-deploy` step (git baked in) | **CI** on push |
| `cashato/frontend` | `Dockerfile.frontend` | the SPA — node build stage → hardened nginx | **CI** on push |
| `cashato/train` | `Dockerfile.train` | retrain CronJob + champion import Job (torch CPU, embed model pre-downloaded) | `scripts/build-images.sh` |
| `cashato/predict` | `Dockerfile.predict` | KServe custom predictor; pulls the model from MLflow `@champion` at startup | `scripts/build-images.sh` |
| `cashato/mlflow` | `Dockerfile.mlflow` | MLflow tracking + registry (adds psycopg2 for CNPG, boto3 for MinIO) | `scripts/build-images.sh` |

Shared choices:

- **`config/*.yaml` is never baked in** — it mounts as a ConfigMap at
  `CASHATO_CONFIG_DIR`, so a config edit deploys without a rebuild.
- The CI-built images run as **non-root**: `svc`/`migrate` set `USER 1000`,
  the frontend uses `nginx-unprivileged` (UID 101, read-only root FS friendly).
- Build context is the **repo root** (the root `.dockerignore` applies):
  `docker build -f docker/Dockerfile.svc .` — or just `scripts/build-images.sh`
  to build all six and `kind load` them.
- CI pushes `gitea-http.gitea.svc:3000/cashato/<img>:<commit-sha>`; the kind
  nodes resolve that registry through a containerd mirror.
