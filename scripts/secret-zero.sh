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
  } > "$PW"
  chmod 600 "$PW"
fi

echo "secret-zero ready in $DIR (gitignored). BACK IT UP OUT-OF-BAND."
echo "next: tofu apply (installs the pinned key) && ./scripts/seal-secrets.sh"
