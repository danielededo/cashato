#!/usr/bin/env bash
# Build the bootstrap container images and load them into the kind cluster.
# This is the manual image path; CI (Tekton) builds and pushes the svc/migrate
# images on every push to main.
#
# Images:
#   cashato/svc:dev     — the 3 services (ingest-api, etl-worker, query-api)
#   cashato/migrate:dev — Alembic migrator (migration Job + grant Job)
#   cashato/mlflow:dev  — MLflow tracking server + registry
#   cashato/train:dev   — training/retrain + model import (heavy: torch + ST)
#   cashato/predict:dev — KServe custom predictor (heavy: torch + ST + kserve)
#
# Run after changing service/migrator code or dependencies, then restart the
# affected Deployments so they pick up the reloaded :dev tag.
#
# Prereqs: docker + kind in PATH.
# Usage:   ./scripts/build-images.sh
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
CLUSTER="${KIND_CLUSTER:-cashato}"

build_load() { # <image:tag> <dockerfile-name>
  echo "==> build $1"
  docker build -t "$1" -f "$ROOT/docker/$2" "$ROOT"
  echo "==> kind load $1 (cluster: $CLUSTER)"
  kind load docker-image "$1" --name "$CLUSTER"
}

build_load cashato/svc:dev      Dockerfile.svc
build_load cashato/migrate:dev  Dockerfile.migrate
build_load cashato/frontend:dev Dockerfile.frontend
build_load cashato/mlflow:dev   Dockerfile.mlflow

# train/predict BAKE models/latest.joblib, which is gitignored: on a fresh clone
# it does not exist yet, and under `set -e` an unconditional build here aborted
# the script after the four images above — looking like a failure when the
# platform was in fact ready. Skip instead, and say so: a serving image with no
# model to serve is not useful anyway. Train one (README, "ML pipeline") and
# re-run to get them.
if [[ -f "$ROOT/models/latest.joblib" ]]; then
  build_load cashato/train:dev   Dockerfile.train
  build_load cashato/predict:dev Dockerfile.predict
else
  echo "==> SKIP cashato/train:dev and cashato/predict:dev"
  echo "    models/latest.joblib is absent (gitignored; produced by cashato.ml.train)."
  echo "    The four images the platform needs are built and loaded."
fi

echo "done. kind reloaded the :dev tags — restart pods to use them, e.g.:"
echo "  kubectl -n cashato rollout restart deploy"
