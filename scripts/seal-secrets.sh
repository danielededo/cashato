#!/usr/bin/env bash
# Regenerate the committed SealedSecrets DETERMINISTICALLY from the secret-zero
# material (infra/secrets/: the pinned public cert + the role passwords). Sealing
# is done OFFLINE against the cert file, so no running cluster / no --fetch-cert is
# needed and the procedure is fully reproducible.
#
# The plaintext (passwords) never leaves infra/secrets/; only the encrypted
# SealedSecret YAMLs are written into k8s/ and committed to git.
#
# NOTE: kubeseal uses a fresh random session key each run, so the encryptedData
# bytes differ every time even for identical input (the DECRYPTED value is the
# same). Re-run only when a password actually changes, to avoid pointless git
# churn.
#
# Prereqs: kubeseal in PATH; ./scripts/secret-zero.sh already run.
# Usage:   ./scripts/seal-secrets.sh
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
CERT="$ROOT/infra/secrets/sealed-secrets.crt"
PW="$ROOT/infra/secrets/role-passwords.env"
MINIO="$ROOT/infra/secrets/minio-creds.env"
DATA="$ROOT/k8s/manifests/data/base"
SERVICES="$ROOT/k8s/manifests/services/base"
MINIO_DIR="$ROOT/k8s/manifests/minio/base"
MLFLOW_DIR="$ROOT/k8s/manifests/mlflow/base"
TRAINING_DIR="$ROOT/k8s/manifests/training/base"
OBS_DIR="$ROOT/k8s/manifests/observability/base"
TEKTON_DIR="$ROOT/k8s/manifests/tekton-ci/base"
WEBHOOK="$ROOT/infra/secrets/webhook-secret.env"
TFVARS="$ROOT/infra/secret.auto.tfvars"
mkdir -p "$DATA" "$SERVICES" "$MINIO_DIR" "$MLFLOW_DIR" "$TRAINING_DIR" "$OBS_DIR"

[[ -f "$CERT" ]]  || { echo "missing $CERT — run ./scripts/secret-zero.sh first"; exit 1; }
[[ -f "$PW" ]]    || { echo "missing $PW — run ./scripts/secret-zero.sh first"; exit 1; }
[[ -f "$MINIO" ]] || { echo "missing $MINIO — run ./scripts/secret-zero.sh first"; exit 1; }
[[ -f "$WEBHOOK" ]] || { echo "missing $WEBHOOK — run ./scripts/secret-zero.sh first"; exit 1; }
# shellcheck disable=SC1090
source "$PW"     # -> $etl_writer, $query_reader, $mlflow, $ml_reader, $grafana_admin
# shellcheck disable=SC1090
source "$MINIO"  # -> $minio_user, $minio_password

seal() { # <secret-name> <namespace> <username> <password> <out-file>
  kubectl create secret generic "$1" -n "$2" \
    --type=kubernetes.io/basic-auth \
    --from-literal=username="$3" --from-literal=password="$4" \
    --dry-run=client -o yaml \
  | kubeseal --cert "$CERT" --format yaml > "$5"
  echo "sealed $2/$1 -> ${5#"$ROOT"/}"
}

seal_literal() { # <secret-name> <namespace> <out-file> <key=value>...
  local name="$1" ns="$2" out="$3"; shift 3
  local args=(); local kv
  for kv in "$@"; do args+=(--from-literal="$kv"); done
  kubectl create secret generic "$name" -n "$ns" "${args[@]}" \
    --dry-run=client -o yaml \
  | kubeseal --cert "$CERT" --format yaml > "$out"
  echo "sealed $ns/$name -> ${out#"$ROOT"/}"
}

# Same, but for keys whose value is a FILE body (newlines, leading dots). Written
# to a private temp dir because --from-file takes the basename as the key.
seal_files() { # <secret-name> <namespace> <out-file> <key=body>...
  local name="$1" ns="$2" out="$3"; shift 3
  local tmp; tmp="$(mktemp -d)"; chmod 700 "$tmp"
  local args=(); local kv key
  for kv in "$@"; do
    key="${kv%%=*}"
    printf '%s' "${kv#*=}" > "$tmp/$key"
    args+=(--from-file="$tmp/$key")
  done
  kubectl create secret generic "$name" -n "$ns" "${args[@]}" \
    --dry-run=client -o yaml \
  | kubeseal --cert "$CERT" --format yaml > "$out"
  rm -rf "$tmp"
  echo "sealed $ns/$name -> ${out#"$ROOT"/}"
}

# git_bridge_* live in secret.auto.tfvars (Tofu consumes them too), not in
# infra/secrets/ — so they are read out of the HCL rather than sourced.
val() { { grep -i "^$1" "$TFVARS" 2>/dev/null || true; } | sed 's/.*=[[:space:]]*//; s/"//g' | tr -d ' '; }

# DB roles (cashato-data namespace, consumed by CNPG managed.roles).
seal etl-writer-db   cashato-data etl_writer   "$etl_writer"   "$DATA/sealedsecret-etl-writer.yaml"
seal query-reader-db cashato-data query_reader "$query_reader" "$DATA/sealedsecret-query-reader.yaml"

# Same role creds, copied into the services namespace (cashato) — sealed-secrets
# strict scope is name+namespace-bound, so each namespace needs its own sealing.
# ingest-api + etl-worker connect as etl_writer; query-api as query_reader.
seal etl-writer-db   cashato etl_writer   "$etl_writer"   "$SERVICES/sealedsecret-etl-writer.yaml"
seal query-reader-db cashato query_reader "$query_reader" "$SERVICES/sealedsecret-query-reader.yaml"

# MinIO creds: same username/password sealed for the server (ns minio, mapped to
# MINIO_ROOT_USER/PASSWORD) and the clients (ns cashato, mapped to
# MINIO_ACCESS_KEY/SECRET_KEY in the deployments).
seal minio-creds minio   "$minio_user" "$minio_password" "$MINIO_DIR/sealedsecret-minio.yaml"
seal minio-creds cashato "$minio_user" "$minio_password" "$SERVICES/sealedsecret-minio.yaml"
# ...and for CNPG, whose WAL archiving and ScheduledBackup write to the same
# bucket (cluster.yaml + backup-bucket-job.yaml read it). This one was committed
# but never regenerated here, so a fork's backups failed to decrypt with no
# script to fix it.
seal minio-creds cashato-data "$minio_user" "$minio_password" "$DATA/sealedsecret-minio.yaml"

# MLflow DB role (mlflow): sealed for CNPG (ns cashato-data, managed.roles password)
# and for the MLflow server (ns mlflow). Also give MLflow the MinIO creds (artifacts).
seal mlflow-db cashato-data mlflow "$mlflow" "$DATA/sealedsecret-mlflow-db.yaml"
seal mlflow-db mlflow       mlflow "$mlflow" "$MLFLOW_DIR/sealedsecret-mlflow-db.yaml"
seal minio-creds mlflow "$minio_user" "$minio_password" "$MLFLOW_DIR/sealedsecret-minio.yaml"

# ml_reader role (read-only silver+gold): sealed for CNPG (ns cashato-data,
# managed.roles password) and for the training/retrain Jobs (ns cashato-ml).
seal ml-reader-db cashato-data ml_reader "$ml_reader" "$DATA/sealedsecret-ml-reader.yaml"
seal ml-reader-db cashato-ml   ml_reader "$ml_reader" "$TRAINING_DIR/sealedsecret-ml-reader.yaml"

# Observability: Mimir uses the MinIO creds for its S3 (blocks) backend;
# Grafana's admin login comes from grafana-admin (username "admin" + password).
seal minio-creds   observability "$minio_user" "$minio_password" "$OBS_DIR/sealedsecret-minio.yaml"
seal grafana-admin observability admin         "$grafana_admin"  "$OBS_DIR/sealedsecret-grafana-admin.yaml"

# --- CI (ns cashato-ci) -----------------------------------------------------
# All four derive from the SAME two inputs: the Gitea bridge credential (which
# Tofu also consumes, hence its home in secret.auto.tfvars rather than
# infra/secrets/) and the webhook token. Nothing here is a new secret; they are
# projections of those two into the shapes Tekton and buildah expect.
GITEA_HOST="gitea-http.gitea.svc:3000"
gitea_user="$(val git_bridge_username)"; gitea_user="${gitea_user:-cashato}"
gitea_pw="$(val git_bridge_password)"
[[ -n "$gitea_pw" ]] || { echo "missing git_bridge_password in $TFVARS — run ./scripts/secret-zero.sh first"; exit 1; }
# shellcheck disable=SC1090
source "$WEBHOOK"  # -> $webhook_secret

# Gitea admin: the webhook-creating Job authenticates to the Gitea API with it.
seal gitea-admin cashato-ci "$gitea_user" "$gitea_pw" "$TEKTON_DIR/sealedsecret-gitea-admin.yaml"

# The HMAC token, shared by the Gitea webhook and the EventListener interceptor.
seal_literal gitea-webhook-secret cashato-ci \
  "$TEKTON_DIR/sealedsecret-webhook-secret.yaml" "secretToken=$webhook_secret"

# git over HTTP for git-clone and for bump-deploy's push: the store helper reads
# the credential out of ~/.git-credentials, which the Task copies from this
# workspace. Host-scoped, so it can never be offered to another remote.
seal_files gitea-basic-auth cashato-ci "$TEKTON_DIR/sealedsecret-git-basic-auth.yaml" \
  ".gitconfig=$(printf '[credential]\n\thelper = store\n')" \
  ".git-credentials=http://${gitea_user}:${gitea_pw}@${GITEA_HOST}"

# buildah push: a Docker config with one registry entry. TLSVERIFY=false in the
# Task, so this is plain http to the in-cluster registry.
seal_literal gitea-dockerconfig cashato-ci \
  "$TEKTON_DIR/sealedsecret-dockerconfig.yaml" \
  "config.json={\"auths\":{\"${GITEA_HOST}\":{\"auth\":\"$(printf '%s:%s' "$gitea_user" "$gitea_pw" | base64 -w0)\"}}}"

echo "done. review + commit the regenerated SealedSecrets."
