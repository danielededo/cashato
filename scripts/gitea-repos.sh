#!/usr/bin/env bash
# Ensure the two Gitea repos exist and seed the config repo (C7c-e).
#
#   cashato         — the SOURCE repo (code + manifests). Humans push here; the CI
#                     clones it. (This finally scripts a step that used to be a
#                     manual rebuild-recipe action.)
#   cashato-deploy  — the CONFIG/deploy repo Argo watches. Holds a copy of the
#                     app-of-apps (k8s/apps/); the CI's bump-deploy step pins the
#                     services/data image tags here. Seeded from the source's
#                     k8s/apps/ (the canonical human-authored copy).
#
# Idempotent: existing repos are left as-is; re-seeding pushes the current
# k8s/apps/ (which RESETS the two CI-pinned image tags back to the :dev seed — the
# next code build re-pins them; only relevant after a rare structural app change).
#
# Prereqs: the gitea-http port-forward on localhost:3000
#   (kubectl -n gitea port-forward svc/gitea-http 3000:3000).
# Creds: Gitea admin from infra/secret.auto.tfvars (git_bridge_username/password),
#   falling back to the cashato/cashato-admin-pw defaults.
# Usage: ./scripts/gitea-repos.sh
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
GITEA_URL="${GITEA_URL:-http://localhost:3000}"
TFVARS="$ROOT/infra/secret.auto.tfvars"

val() { { grep -i "^$1" "$TFVARS" 2>/dev/null || true; } | sed 's/.*=[[:space:]]*//; s/"//g' | tr -d ' '; }
USER="$(val git_bridge_username)"; USER="${USER:-cashato}"
PW="$(val git_bridge_password)"
# No fallback: a wrong guessable default silently "working" against a bridge
# that was provisioned with another password is worse than failing here.
if [[ -z "$PW" ]]; then
  echo "ERROR: git_bridge_password not found in $TFVARS" >&2
  exit 1
fi

api() { curl -sS -u "$USER:$PW" -H 'Content-Type: application/json' "$@"; }

# Git credentials via a helper, NOT embedded in the remote URL: an URL with
# the password lands in .git/config, process listings and shell history.
export _GIT_CRED_USER="$USER" _GIT_CRED_PW="$PW"
CRED_HELPER='!f() { printf "username=%s\npassword=%s\n" "$_GIT_CRED_USER" "$_GIT_CRED_PW"; }; f'

ensure_repo() { # <name>
  local name="$1" code
  code="$(api -o /dev/null -w '%{http_code}' "$GITEA_URL/api/v1/repos/$USER/$name")"
  if [[ "$code" == "200" ]]; then
    echo "repo $name: already exists"
  else
    api -X POST "$GITEA_URL/api/v1/user/repos" \
      -d "{\"name\":\"$name\",\"private\":true,\"auto_init\":false}" >/dev/null
    echo "repo $name: created"
  fi
}

ensure_repo cashato
ensure_repo cashato-deploy

# Seed cashato-deploy with the source app-of-apps (k8s/apps/).
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
git -c credential.helper="$CRED_HELPER" clone -q "$GITEA_URL/$USER/cashato-deploy.git" "$tmp"
git -C "$tmp" config credential.helper "$CRED_HELPER"
mkdir -p "$tmp/k8s/apps"
cp "$ROOT"/k8s/apps/*.yaml "$tmp/k8s/apps/"
cd "$tmp"
git add -A
if git -c user.email=ci@cashato.local -c user.name=cashato-seed \
     commit -q -m "seed: app-of-apps from source k8s/apps"; then
  git push -q origin HEAD:main
  echo "cashato-deploy: seeded k8s/apps/ ($(ls k8s/apps | wc -l) apps)"
else
  echo "cashato-deploy: already up to date"
fi
