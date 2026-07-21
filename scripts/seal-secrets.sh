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
mkdir -p "$DATA" "$SERVICES" "$MINIO_DIR" "$MLFLOW_DIR" "$TRAINING_DIR" "$OBS_DIR"

[[ -f "$CERT" ]]  || { echo "missing $CERT — run ./scripts/secret-zero.sh first"; exit 1; }
[[ -f "$PW" ]]    || { echo "missing $PW — run ./scripts/secret-zero.sh first"; exit 1; }
[[ -f "$MINIO" ]] || { echo "missing $MINIO — run ./scripts/secret-zero.sh first"; exit 1; }
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

# MLflow DB role (mlflow): sealed for CNPG (ns cashato-data, managed.roles password)
# and for the MLflow server (ns mlflow). Also give MLflow the MinIO creds (artifacts).
seal mlflow-db cashato-data mlflow "$mlflow" "$DATA/sealedsecret-mlflow-db.yaml"
seal mlflow-db mlflow       mlflow "$mlflow" "$MLFLOW_DIR/sealedsecret-mlflow-db.yaml"
seal minio-creds mlflow "$minio_user" "$minio_password" "$MLFLOW_DIR/sealedsecret-minio.yaml"

# ml_reader role (read-only silver+gold): sealed for CNPG (ns cashato-data,
# managed.roles password) and for the training/retrain Jobs (ns cashato-ml).
seal ml-reader-db cashato-data ml_reader "$ml_reader" "$DATA/sealedsecret-ml-reader.yaml"
seal ml-reader-db cashato-ml   ml_reader "$ml_reader" "$TRAINING_DIR/sealedsecret-ml-reader.yaml"

# Observability (C7a): Mimir uses the MinIO creds for its S3 (blocks) backend;
# Grafana's admin login comes from grafana-admin (username "admin" + password).
seal minio-creds   observability "$minio_user" "$minio_password" "$OBS_DIR/sealedsecret-minio.yaml"
seal grafana-admin observability admin         "$grafana_admin"  "$OBS_DIR/sealedsecret-grafana-admin.yaml"

echo "done. review + commit the regenerated SealedSecrets."
