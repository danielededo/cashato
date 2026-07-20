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
DATA="$ROOT/k8s/manifests/data/base"

[[ -f "$CERT" ]] || { echo "missing $CERT — run ./scripts/secret-zero.sh first"; exit 1; }
[[ -f "$PW" ]]   || { echo "missing $PW — run ./scripts/secret-zero.sh first"; exit 1; }
# shellcheck disable=SC1090
source "$PW"   # -> $etl_writer, $query_reader

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

# (C5c will add the cashato-namespace copies for the services here.)
echo "done. review + commit the regenerated SealedSecrets."
