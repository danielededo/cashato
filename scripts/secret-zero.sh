#!/usr/bin/env bash
# Bootstrap the "secret zero" for the platform: the PINNED Sealed Secrets keypair
# and the DB role passwords. This is the single out-of-band root of trust — it
# lives in infra/secrets/ (gitignored) and must be backed up separately; it is
# NEVER committed. Everything else (the sealed secrets, the cluster) is
# reproducible from git + this material.
#
# Idempotent: only creates files that don't already exist, so re-running never
# clobbers an existing key (which would invalidate every committed SealedSecret)
# or rotates passwords by accident. To rotate, delete the file first, then re-run
# this and seal-secrets.sh.
#
# Usage:  ./scripts/secret-zero.sh
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
DIR="$ROOT/infra/secrets"
mkdir -p "$DIR"

CRT="$DIR/sealed-secrets.crt"
KEY="$DIR/sealed-secrets.key"
if [[ -f "$CRT" && -f "$KEY" ]]; then
  echo "[keep] sealing keypair already present"
else
  echo "[gen]  sealing keypair (RSA 4096, self-signed, 10y)"
  openssl req -x509 -nodes -newkey rsa:4096 -days 3650 \
    -keyout "$KEY" -out "$CRT" -subj "/CN=sealed-secret/O=sealed-secret" 2>/dev/null
  chmod 600 "$KEY"
fi

PW="$DIR/role-passwords.env"
if [[ -f "$PW" ]]; then
  echo "[keep] role passwords already present"
else
  echo "[gen]  role passwords (hex, URL-safe)"
  {
    echo "etl_writer=$(openssl rand -hex 24)"
    echo "query_reader=$(openssl rand -hex 24)"
    echo "mlflow=$(openssl rand -hex 24)"
    echo "ml_reader=$(openssl rand -hex 24)"
    echo "grafana_admin=$(openssl rand -hex 24)"
  } > "$PW"
  chmod 600 "$PW"
fi

# Idempotent append: never rewrites an existing line.
if ! grep -q '^ml_reader=' "$PW"; then
  echo "[gen]  ml_reader password (appended)"
  echo "ml_reader=$(openssl rand -hex 24)" >> "$PW"
fi
if ! grep -q '^grafana_admin=' "$PW"; then
  echo "[gen]  grafana_admin password (appended)"
  echo "grafana_admin=$(openssl rand -hex 24)" >> "$PW"
fi

MINIO="$DIR/minio-creds.env"
if [[ -f "$MINIO" ]]; then
  echo "[keep] minio creds already present"
else
  echo "[gen]  minio creds"
  {
    echo "minio_user=cashato"
    echo "minio_password=$(openssl rand -hex 24)"
  } > "$MINIO"
  chmod 600 "$MINIO"
fi

# Platform bootstrap secret consumed by Tofu (NOT a SealedSecret: Argo needs the
# Gitea repo credential to start, before the controller can decrypt anything).
# Auto-loaded by Tofu as *.auto.tfvars; gitignored.
TFVARS="$ROOT/infra/secret.auto.tfvars"
if [[ -f "$TFVARS" ]]; then
  echo "[keep] $TFVARS already present"
else
  echo "[gen]  infra/secret.auto.tfvars (Gitea/Argo bridge password)"
  {
    echo "# Out-of-band platform secrets (gitignored). Part of secret-zero."
    echo "git_bridge_password = \"$(openssl rand -hex 24)\""
  } > "$TFVARS"
  chmod 600 "$TFVARS"
  echo "       NOTE: on a fresh env, set the Gitea git remote to use this password."
fi

echo "secret-zero ready ($DIR + infra/secret.auto.tfvars, gitignored). BACK UP OUT-OF-BAND."
echo "next: tofu apply (installs the pinned key) && ./scripts/seal-secrets.sh"
