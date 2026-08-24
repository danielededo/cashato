# scripts/ — tracked operational procedures

Every manual/author-time operation the platform needs is captured here as a
committed script, so nothing is undocumented "tribal knowledge". Scripts read the
**secret zero** (out-of-band material in `infra/secrets/`, gitignored) and produce
reproducible, committable artifacts.

The rule: **git + `infra/secrets/` (backed up) must be enough to rebuild
everything.** No ad-hoc commands.

## Secrets

| Script | What it does | When to run |
|--------|--------------|-------------|
| `secret-zero.sh` | Generate the pinned Sealed Secrets keypair, the DB role passwords and the MinIO credentials into `infra/secrets/` — **plus `infra/secret.auto.tfvars`** (`git_bridge_password`, the one Tofu variable with no default). Idempotent, never clobbers. The single root of trust. | Once per environment (or restore the backup instead). |
| `seal-secrets.sh` | Regenerate **13 of the 18** committed `SealedSecret` YAMLs from `infra/secrets/`, offline against the cert file. See the gap below. | After a password change, or on a fresh checkout with the restored secret-zero. |

**Flow:** `secret-zero.sh` → `tofu apply` (installs the pinned key into the
controller) → `seal-secrets.sh` → commit the SealedSecrets → Argo applies them →
the controller decrypts them in-cluster.

> **The rule above is not yet true.** `seal-secrets.sh` does not cover
> `k8s/manifests/data/base/sealedsecret-minio.yaml` (CNPG backups) nor the four
> under `k8s/manifests/tekton-ci/base/` (`dockerconfig`, `git-basic-auth`,
> `gitea-admin`, `webhook-secret` — the whole CI loop), and nothing tracked
> generates `infra/secrets/webhook-secret.env`. On a fork those five stay
> encrypted to the upstream key and must be re-sealed by hand with `kubeseal`.
> Until the script covers all eighteen, "git + `infra/secrets/`" rebuilds the
> application but not the CI or the backups.

## Git repos (GitOps bridge)

| Script | What it does | When to run |
|--------|--------------|-------------|
| `gitea-repos.sh` | Idempotently create the `cashato` (source) and `cashato-deploy` (config) Gitea repos via the API, and seed `cashato-deploy` from the source `k8s/apps/`. **Needs** the port-forward `kubectl -n gitea port-forward svc/gitea-http 3000:3000` and `git_bridge_password` in `infra/secret.auto.tfvars` — it exits 1 rather than fall back to a guessable default. | On a fresh cluster (bootstrap), or after a structural app-of-apps change (re-seed; resets CI image pins to `:dev`, next build re-pins). |

## Images

| Script | What it does | When to run |
|--------|--------------|-------------|
| `build-images.sh` | Build all **six** `cashato/*:dev` images from `docker/` and `kind load` them. **On a fresh clone it aborts at the 5th**: `Dockerfile.train` bakes `models/latest.joblib`, which is gitignored, and the script is `set -e`. The four the platform needs are built before it, so only `train`/`predict` are missing — and they are useless without a trained model anyway. | Bootstrap / fast local iteration. In steady state the **CI (Tekton) builds+deploys `svc`, `migrate` and `frontend` by SHA** on every push; this script remains the path for `train`/`predict`/`mlflow` (out of CI scope) and for a from-scratch `:dev` load. |

## MLOps — retraining

The categorization model lives in the **MLflow registry** (`cashato-categorizer`,
alias `@champion` = what serving uses), not as a loose file. Retraining is
in-cluster and tracked — no host scripts:

| Action | How |
|--------|-----|
| Import the existing model as the incumbent | `register-champion` Job (runs automatically on sync; idempotent `--if-absent`). |
| Retrain on a schedule | Unsuspend the `train` CronJob (`kubectl -n cashato-ml patch cronjob train -p '{"spec":{"suspend":false}}'`). |
| Retrain on demand | `kubectl -n cashato-ml create job --from=cronjob/train train-manual-$(date +%s)`. |
| Enrich the dataset (long tail) | host + Ollama: `python -m cashato.ml.label_llm` (offline, GPU) → `gold.training_labels`. |

Each retrain trains a *challenger*, registers a new version, and promotes it to
`@champion` only if it beats the current champion on the holdout (`--promote
if-better`) — a bad retrain never regresses serving.

`infra/secrets/` holds: `sealed-secrets.crt` / `.key` (pinned sealing key) and
`role-passwords.env`. It is gitignored — **back it up out-of-band**; losing it
means re-sealing every secret.

## Manual procedures (not scripted)

These remain manual:

- **cluster rebuild** (kind delete → state reset → `tofu apply` → `build-images.sh`
  → `gitea-repos.sh` → `git push` → CI re-pins the SHA images).
- **workstation tools** install (`kubeseal`, `kind` to `~/.local/bin`).
