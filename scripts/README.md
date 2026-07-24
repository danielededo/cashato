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
| `secret-zero.sh` | Generate the pinned Sealed Secrets keypair + DB role passwords into `infra/secrets/` (idempotent, never clobbers). The single root of trust. | Once per environment (or restore the backup instead). |
| `seal-secrets.sh` | Regenerate the committed `SealedSecret` YAMLs from `infra/secrets/`, offline against the cert file. | After a password change, or on a fresh checkout with the restored secret-zero. |

**Flow:** `secret-zero.sh` → `tofu apply` (installs the pinned key into the
controller) → `seal-secrets.sh` → commit the SealedSecrets → Argo applies them →
the controller decrypts them in-cluster.

## Git repos (GitOps bridge)

| Script | What it does | When to run |
|--------|--------------|-------------|
| `gitea-repos.sh` | Idempotently create the `cashato` (source) and `cashato-deploy` (config) Gitea repos via the API, and seed `cashato-deploy` from the source `k8s/apps/`. | On a fresh cluster (bootstrap), or after a structural app-of-apps change (re-seed; resets CI image pins to `:dev`, next build re-pins). |

## Images

| Script | What it does | When to run |
|--------|--------------|-------------|
| `build-images.sh` | Build all five `cashato/*:dev` images from `build/` and `kind load` them. | Bootstrap / fast local iteration. In steady state the **CI (Tekton) builds+deploys `svc`+`migrate` by SHA** on every push (C7c); this script is still the path for the heavy `train`/`predict`/`mlflow` images (out of CI scope) and for a from-scratch `:dev` load. |

## MLOps — retraining (C6)

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

## Still to be captured as scripts (tracked TODO)

These manual procedures are documented in the memory/rebuild recipe and will move
here as we formalize them:

- **cluster rebuild** (kind delete → state reset → `tofu apply` → `build-images.sh`
  → `gitea-repos.sh` → `git push` → CI re-pins the SHA images).
- **workstation tools** install (`kubeseal`, `kind` to `~/.local/bin`).
