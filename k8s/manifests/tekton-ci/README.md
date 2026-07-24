# Tekton CI (`cashato-ci`) — C7c-c

Continuous integration for the cashato monorepo: on every commit it lints,
type-checks, tests, then builds and pushes the two container images to Gitea's
built-in OCI registry. Delivered as GitOps (Argo app `tekton-ci`, sync-wave 7);
the pipeline definitions live here, a run is a separate on-demand object.

## Pipeline DAG

```
fetch-source ──▶ lint-test ──┬──▶ build-push-svc      (cashato/svc)
                             └──▶ build-push-migrate  (cashato/migrate)
```

| Task | What it does | Source |
|------|--------------|--------|
| `fetch-source` | shallow clone of the repo (private → `basic-auth` workspace) | hub: `git-clone` 0.9 |
| `lint-test` | `pip install .[svc,dev]` → `ruff check .` → `mypy src` → `pytest` (mirrors the [Makefile](../../../Makefile) targets) | local Task `cashato-lint-test` |
| `build-push-svc` | build+push `cashato/svc` | hub: `buildah` 0.9 |
| `build-push-migrate` | build+push `cashato/migrate` | hub: `buildah` 0.9 |

The two builds run **in parallel** once `lint-test` passes (both `runAfter: [lint-test]`),
so a lint/test failure blocks *all* image publishing.

## Which services depend on each image

This is the key coupling: the CI builds **2 images**, but they back **6 workloads**
across 2 namespaces. Change any of the code below and that image (and its consumers)
is what a green pipeline republishes.

| Built image | Dockerfile | Consumed by | Namespace |
|-------------|-----------|-------------|-----------|
| **`cashato/svc`** | `build/Dockerfile.svc` | `ingest-api`, `etl-worker`, `query-api`, `categorizer` (Deployments) | `cashato` |
| **`cashato/migrate`** | `build/Dockerfile.migrate` | `migration-job`, `grant-job` (Jobs) | `cashato-data` |

`cashato/svc` is the **shared** service image (one image, four Deployments — they
differ only by command/env, see `k8s/manifests/services/`). `cashato/migrate`
carries Alembic + the DB tooling for the migration and grant Jobs
(`k8s/manifests/data/`).

> Out of CI scope (built manually via `scripts/build-images.sh`): the heavy
> `cashato/train`, `cashato/predict`, `cashato/mlflow` images (torch/ST — too slow
> for buildah-on-kind). They can be added as further `build-push-*` tasks later.

## Image tags & registry

Images are pushed to Gitea's OCI registry, tagged by **commit SHA**:

```
gitea-http.gitea.svc:3000/cashato/svc:<commit-sha>
gitea-http.gitea.svc:3000/cashato/migrate:<commit-sha>
```

That ref is exactly what the nodes' containerd mirror resolves for pulls (C7c-b),
so a pushed image is immediately pullable in-cluster. Registry is plain-HTTP
(`TLSVERIFY=false`); buildah uses `STORAGE_DRIVER=vfs` to build unprivileged in a
pod on kind.

> The service manifests still pin `cashato/svc:dev` / `cashato/migrate:dev`
> (kind-loaded). Wiring them to the CI-published SHA tags is **C7c-e**
> (config-repo split + tag bump) — not done here.

## Running a build

Until Gitea webhooks are wired via Tekton Triggers (**C7c-d**), start a run by hand:

```sh
kubectl -n cashato-ci create -f base/pipelinerun-example.yaml   # `create`, not `apply` (generateName)
kubectl -n cashato-ci get pipelinerun -w                        # or the Tekton Dashboard
```

The `TektonConfig` pruner keeps the last 20 runs.

## Files

| File | Purpose |
|------|---------|
| `base/namespace.yaml` | `cashato-ci` namespace |
| `base/serviceaccount.yaml` | run identity (auth via workspaces, not SA secrets) |
| `base/sealedsecret-git-basic-auth.yaml` | git clone creds (`.gitconfig` + `.git-credentials`) |
| `base/sealedsecret-dockerconfig.yaml` | buildah push creds (`config.json`) |
| `base/task-lint-test.yaml` | the ruff/mypy/pytest Task |
| `base/pipeline.yaml` | the `cashato-ci` Pipeline (DAG above) |
| `base/pipelinerun-example.yaml` | reference PipelineRun (NOT synced — manual trigger) |
| `overlays/kind/` | environment overlay |
