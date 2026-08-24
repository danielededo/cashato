# k8s/ — application desired state (GitOps, Argo CD)

Day-2 application manifests, **delivered by Argo CD in pull** from the git repo.
This is the counterpart to `infra/`: OpenTofu owns the **platform** (cluster +
operators + Argo CD itself), Argo CD owns the **apps** defined here.

```mermaid
flowchart LR
    DEV["git push<br/>(source repo)"] --> WH["Gitea webhook"] --> TK["Tekton pipeline<br/>lint · test · build"]
    TK -->|"push :sha"| REG[("Gitea OCI<br/>registry")]
    TK -->|"pin image tags"| DR["cashato-deploy<br/>config repo"]
    DR --> ARGO["Argo CD<br/>(app-of-apps)"]
    SRC["k8s/ manifests<br/>(this directory)"] --> ARGO
    ARGO -->|"sync"| CL["kind cluster"]
    REG -->|"containerd mirror pull"| CL
```

The source repo stays **human-only**: CI never commits here. Image tags are
pinned in the separate `cashato-deploy` repo, which Argo watches (details:
[`manifests/tekton-ci/`](manifests/tekton-ci/README.md)).

## Running this without the CI loop

Gitea is load-bearing for **build** — the Tekton EventListener declares no
NodePort or LoadBalancer, so only an in-cluster webhook can reach it, and the
images are pulled from Gitea's registry through the containerd mirror. None of
that is needed to just *run* the manifests, and there are two ways to skip it.

**Apply them directly, no Argo, no git at all.** The overlays are ordinary
Kustomize; the `--load-restrictor` is required because the services base
generates its ConfigMap from `config/*.yaml` at the repo root, outside its
kustomize root:

```bash
kubectl kustomize --load-restrictor=LoadRestrictionsNone \
  manifests/data/overlays/kind | kubectl apply -f -    # then the later waves
```

Component order is yours to keep in this mode — the wave table below is the
order the app-of-apps applies for you, and `data` must finish before `services`.
The overlays declare **registry-less** image names at `:dev` (`cashato/svc`,
`cashato/migrate`, `cashato/frontend`, plus `cashato/mlflow` and `cashato/train`
for the ML components); CI is what overwrites them with registry-qualified SHA
tags. So `docker build` followed by `kind load docker-image cashato/<x>:dev` for
the ones you want is enough, and no registry is involved at all.

**Or keep Argo and point it at your own fork.** The `repoURL` in `apps/*.yaml`
is the only thing that binds these Applications to a git host:

```bash
sed -i 's#http://gitea-http.gitea.svc:3000/cashato/cashato.git#https://github.com/<you>/cashato.git#' apps/*.yaml
```

Argo needs no credentials for a public repo. `apps/` is a copy by design (the
root app watches it in `cashato-deploy`), so editing yours is the intended seam,
not a workaround.

Either way you must generate **your own** secrets first: the committed
`SealedSecret`s are encrypted to this cluster's sealing key and another
controller cannot decrypt them. See [`scripts/`](../scripts/README.md) —
`secret-zero.sh` then `seal-secrets.sh`.

## Layout

```
k8s/
├─ apps/          # one Argo CD Application per component (app-of-apps children)
└─ manifests/     # the actual Kustomize per component: <x>/{base, overlays/kind}
```

- **Root app-of-apps** is seeded by OpenTofu (`infra/`, argocd-apps chart) and
  watches `apps/` in the `cashato-deploy` repo (seeded from this directory —
  see `scripts/gitea-repos.sh`). It is intentionally not a file here — it is
  the single bootstrap link into GitOps.
- **`apps/`** — each file is an Argo `Application` pointing at
  `manifests/<component>/overlays/kind`. Ordering between components is set with
  the `argocd.argoproj.io/sync-wave` annotation, not filename order.
- **`manifests/`** — one directory per component, each a Kustomize `base` plus an
  `overlays/kind` overlay. (Named `manifests/`, not `components/`, to avoid
  clashing with the Kustomize "Components" feature.)

## Components & sync order

| Component         | wave | Contents |
|-------------------|:----:|----------|
| `cilium-lb`       | −1   | Cilium LB IP pool + L2 announcement policy |
| `data`            | 0    | CNPG `Cluster`, managed roles, Alembic migration Job, grants Job |
| `gateway`         | 1    | Envoy Gateway `Gateway` (the services attach their HTTPRoutes) |
| `minio`           | 1    | S3 backend for MLflow artifacts and the LGTM stores |
| `services`        | 2    | ingest-api, etl-worker, query-api, categorizer + HTTPRoutes + config |
| `frontend`        | 2    | the SPA (nginx) + HTTPRoute for `/` |
| `mlflow`          | 2    | model registry + tracking server |
| `networkpolicies` | 3    | Cilium `NetworkPolicy` (DB reachable only by the services that need it) |
| `training`        | 3    | train CronJob (suspended) + register-champion Job |
| `serving`         | 4    | KServe `InferenceService` (the categorizer's model) |
| `observability`   | 5    | LGTM: Loki, Grafana, Tempo, Mimir + Alloy collector |
| `tekton`          | 6    | Tekton operator config (pipelines + triggers) |
| `tekton-ci`       | 7    | the CI pipeline, triggers, EventListener, webhook Job |

The data layer (wave 0) — Cluster + migrations — completes before the services
(wave 2) start: the "migrate-then-deploy" barrier. DB Jobs are tracked
resources with `Replace=true`, not Sync hooks, so a new migrate image produces
visible drift and actually re-runs.
