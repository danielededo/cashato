# k8s/ — application desired state (GitOps, Argo CD)

Day-2 application manifests, **delivered by Argo CD in pull** from the git repo
(Gitea locally now; public GitHub later — only the Argo repoURL changes). This is
the counterpart to `infra/`: OpenTofu owns the **platform** (cluster + operators
+ Argo CD itself), Argo CD owns the **apps** defined here.

## Layout

```
k8s/
├─ apps/          # one Argo CD Application per component (app-of-apps children)
└─ manifests/     # the actual Kustomize per component: <x>/{base, overlays/kind}
```

- **Root app-of-apps** is seeded by OpenTofu (`infra/`, argocd-apps chart) and
  watches `k8s/apps/`. It is intentionally not a file here — it is the single
  bootstrap link into GitOps.
- **`apps/`** — each file is an Argo `Application` pointing at
  `manifests/<component>/overlays/kind`. Ordering between components is set with
  the `argocd.argoproj.io/sync-wave` annotation, not filename order.
- **`manifests/`** — one directory per component, each a Kustomize `base` plus an
  `overlays/kind` overlay. (Named `manifests/`, not `components/`, to avoid
  clashing with the Kustomize "Components" feature.)

## Components & sync order

| Component        | sync-wave | Contents                                        |
|------------------|-----------|-------------------------------------------------|
| `data`           | 0         | CNPG `Cluster` + managed roles + Alembic migration Job |
| `gateway`        | 1         | Envoy Gateway `Gateway` + `HTTPRoute`           |
| `networkpolicies`| 1         | Cilium `NetworkPolicy` (DB reachable only by etl/query) |
| `ingest-api`     | 2         | Deployment + Service                             |
| `etl-worker`     | 2         | Deployment                                       |
| `query-api`      | 2         | Deployment + Service                             |
| `frontend`       | 3         | Deployment + Service                             |

The data layer (wave 0) — Cluster + migrations — completes before services
(wave 2) start: the "migrate-then-deploy" barrier.
