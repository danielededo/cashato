#!/usr/bin/env bash
# Build the bootstrap container images and load them into the kind cluster.
# This is the manual image path used until CI (Tekton) + a registry (Harbor)
# take over in C7 — after which only the image reference in the manifests
# changes, not this workflow.
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
build_load cashato/train:dev    Dockerfile.train
build_load cashato/predict:dev  Dockerfile.predict

echo "done. kind reloaded the :dev tags — restart pods to use them, e.g.:"
echo "  kubectl -n cashato rollout restart deploy"
