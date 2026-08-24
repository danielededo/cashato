# frontend/ — the cashato SPA

![React](https://img.shields.io/badge/React-61DAFB?logo=react&logoColor=black)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?logo=typescript&logoColor=white)
![Vite](https://img.shields.io/badge/Vite-646CFF?logo=vite&logoColor=white)
![Recharts](https://img.shields.io/badge/Recharts-22B5BF)
![nginx](https://img.shields.io/badge/nginx-009639?logo=nginx&logoColor=white)

React + Vite + TypeScript single-page app, served by a hardened nginx behind
the Envoy Gateway at `/`. The APIs live under `/api/v1` on the **same origin**
(the gateway path-splits), so the app only ever calls relative URLs.

Because the gateway does that split, the image ships **no** API proxy —
`nginx.conf` serves static files and the SPA fallback, nothing else. It does
carry a wildcard `include /etc/nginx/api-proxy/*.conf`, which matches nothing in
the cluster; `compose.yaml` mounts [`api-proxy.conf`](api-proxy.conf) there to
make nginx stand in for the gateway when there is none. That file's path split
mirrors `k8s/manifests/services/base/httproutes.yaml` — if you add a write
endpoint to ingest-api, both lists need it.

## Pages

| Page | What it does |
|------|--------------|
| `Dashboard` | hero net flow, period filters (1M/3M/…), income vs expense, category ranks, heatmap |
| `Transactions` | day-grouped list, full filter set, server-side sort and filtered-set totals, detail drawer |
| `Investments` | two things on one page: **liquid wealth** (declared balances per account + the wealth-over-time area) and **investments** (gross contributed hero, known/unknown split, net-flow chart, per-kind table, holdings) |
| `Review` | low-confidence categorizations, one-click manual correction (feeds active learning) |
| `Upload` | multi-file drag&drop, per-file status from bronze |
| `Manage` | account display names, admin reprocess/reset, the guided offline retrain steps, and the two data-health panels: reconciliation (parsed movements vs declared balances) and coverage (which statement is missing, per source) |

Cross-cutting: IT/EN i18n (`lib/i18n.ts`), light/dark theme, privacy mode
(blurs every monetary surface via CSS, `lib/privacy.tsx`).

## Layout

```
src/
  api/          client.ts (fetch wrapper) · types.ts (TS mirrors of the API models)
  components/   charts.tsx (Recharts, one lazy chunk) · primitives.tsx (Donut, RankBars, …) · TransactionDetail.tsx
  lib/          i18n · lang · privacy · period · format (money/num) · colors · accounts/meta caches
  pages/        one file per page (above)
  styles.css    design tokens + all styling (light-first, dark via [data-theme])
```

Two invariants worth knowing before touching code:

- **Money arrives as strings** (`Money` type): the API serializes `Decimal` as
  JSON strings on purpose. Convert deliberately with `num()` — never assume a
  number.
- **Charts read CSS custom properties** for their colors, so the light/dark
  swap needs no JS.

## Develop

```bash
npm install
npm run dev        # Vite dev server; /api/v1 is proxied to the cluster gateway
npm run build      # tsc --noEmit + vite build (what CI runs)
```

The dev proxy targets the kind gateway LB (`http://172.19.255.1`); override
with `VITE_API_TARGET` (e.g. a port-forward: `VITE_API_TARGET=http://localhost:8080`).

## Ship

CI builds `docker/Dockerfile.frontend` (node build stage → nginx) on every push
to `main` and pins the SHA tag so Argo deploys it — see
[`k8s/manifests/tekton-ci/`](../k8s/manifests/tekton-ci/README.md).
`nginx.conf` serves the immutable hashed assets with long cache and falls back
to `index.html` for SPA routes.
