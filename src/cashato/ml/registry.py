"""MLflow model-registry helpers for the categorizer (MLOps).

Centralizes the three registry operations shared by ``ml/train.py`` (retrain) and
``ml/register_model.py`` (import the existing model):

- **log_and_register** — log a run (params + metrics + the joblib artifact) and
  register a new *version* of the ``cashato-categorizer`` model.
- **load_champion** — download + load the model version currently aliased
  ``@champion`` (what KServe serves), for challenger comparison.
- **set_champion** — move the ``@champion`` alias to a version (promotion).

The tracking URI comes from ``MLFLOW_TRACKING_URI`` (the in-cluster server at
``http://mlflow.mlflow.svc:5000``). When unset, MLflow falls back to local file
tracking so the scripts still run on the host without a cluster.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import mlflow
from mlflow.tracking import MlflowClient

from cashato.ml.model import EmbeddingKNN

MODEL_NAME = "cashato-categorizer"
CHAMPION_ALIAS = "champion"
EXPERIMENT = "cashato-categorizer"
ARTIFACT_PATH = "model"  # subdir under the run holding the model
MODEL_FILE = "emb-knn.joblib"  # filename within the artifact dir


def _init() -> None:
    uri = os.environ.get("MLFLOW_TRACKING_URI")
    if uri:
        mlflow.set_tracking_uri(uri)


def log_and_register(
    model: EmbeddingKNN, params: dict, metrics: dict, run_name: str
) -> int:
    """Log a run and register a new model version. Returns the version number."""
    _init()
    mlflow.set_experiment(EXPERIMENT)
    with tempfile.TemporaryDirectory() as tmp:
        artifact = Path(tmp) / MODEL_FILE
        model.save(artifact)
        with mlflow.start_run(run_name=run_name) as run:
            mlflow.log_params(params)
            if metrics:
                mlflow.log_metrics(metrics)
            mlflow.log_artifact(str(artifact), artifact_path=ARTIFACT_PATH)
            run_id = run.info.run_id
    version = mlflow.register_model(f"runs:/{run_id}/{ARTIFACT_PATH}", MODEL_NAME).version
    return int(version)


def _champion_version() -> str | None:
    client = MlflowClient()
    try:
        return client.get_model_version_by_alias(MODEL_NAME, CHAMPION_ALIAS).version
    except Exception:  # noqa: BLE001 — no model / no alias yet
        return None


def champion_exists() -> bool:
    _init()
    return _champion_version() is not None


def load_champion() -> EmbeddingKNN | None:
    """Download + load the current @champion model, or None if there isn't one."""
    _init()
    if _champion_version() is None:
        return None
    local = mlflow.artifacts.download_artifacts(f"models:/{MODEL_NAME}@{CHAMPION_ALIAS}")
    hits = list(Path(local).rglob(MODEL_FILE))
    return EmbeddingKNN.load(hits[0]) if hits else None


def set_champion(version: int) -> None:
    """Promote a version: point the @champion alias at it (KServe then serves it)."""
    _init()
    MlflowClient().set_registered_model_alias(MODEL_NAME, CHAMPION_ALIAS, str(version))
