"""KServe custom predictor for the categorization model (C6c).

Our model is a custom ``EmbeddingKNN`` (sentence-transformers + kNN), not a
standard framework format, so KServe serves it via a **custom predictor**: this
subclass of ``kserve.Model`` implements load + predict, and the kserve SDK's
``ModelServer`` provides the inference protocol (V1: ``/v1/models/<name>:predict``).

At startup it pulls the model currently aliased ``@champion`` from the MLflow
registry (``MLFLOW_TRACKING_URI``) — so promoting a new champion + restarting the
predictor is all it takes to serve a new model. The heavy deps (torch, ST) live
only in this image; the categorizer (C6d) is a light HTTP client of it.

Request  (V1):  {"instances": ["<raw description>", ...]}   (str or {"description": str})
Response (V1):  {"predictions": [{"category": "<code>", "confidence": <float>}, ...]}
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import kserve

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from libs.parsers.categorize import build_text  # noqa: E402
from ml.registry import load_champion  # noqa: E402

DEFAULT_NAME = "cashato-categorizer"


class CategorizerModel(kserve.Model):
    def __init__(self, name: str):
        super().__init__(name)
        self.name = name
        self._model = None
        self.load()

    def load(self) -> None:
        self._model = load_champion()
        if self._model is None:
            raise RuntimeError("no @champion model in the MLflow registry")
        self.ready = True

    def predict(self, payload: dict, headers: dict | None = None) -> dict:
        instances = payload.get("instances", [])
        texts = [
            build_text(x if isinstance(x, str) else (x.get("description", "")))
            for x in instances
        ]
        preds = self._model.predict_batch(texts)
        out = [
            {"category": p[0], "confidence": round(p[1], 4)}
            if p
            else {"category": "other", "confidence": 0.0}
            for p in preds
        ]
        return {"predictions": out}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-name", default=DEFAULT_NAME)
    args, _ = ap.parse_known_args()
    kserve.ModelServer().start([CategorizerModel(args.model_name)])
