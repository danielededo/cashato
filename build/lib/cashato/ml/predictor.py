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
import os

import kserve

# Pin torch/OpenMP threads to the pod's CPU allotment. Without this torch spawns
# ~half the HOST cores (it can't see the cgroup limit) and thrashes against the
# container CPU cap — making encoding pathologically slow. Match OMP_NUM_THREADS.
_THREADS = int(os.environ.get("OMP_NUM_THREADS", "4"))
try:
    import torch

    torch.set_num_threads(_THREADS)
except Exception:  # noqa: BLE001
    pass


from cashato.ml.model import EmbeddingKNN  # noqa: E402
from cashato.ml.registry import load_champion  # noqa: E402
from cashato.parsers.categorize import build_text  # noqa: E402

DEFAULT_NAME = "cashato-categorizer"


class CategorizerModel(kserve.Model):
    def __init__(self, name: str):
        super().__init__(name)
        self.name = name
        self._model: EmbeddingKNN | None = None
        self.load()

    def load(self) -> None:
        self._model = load_champion()
        if self._model is None:
            raise RuntimeError("no @champion model in the MLflow registry")
        # Warm up: force the (lazy) embedding model to load now, at startup, so the
        # readiness probe only passes once the pod can serve fast — no 20s cold
        # start on the first real request.
        self._model.predict_batch(["warmup"])
        self.ready = True

    def predict(self, payload: dict, headers: dict | None = None) -> dict:
        instances = payload.get("instances", [])
        texts = [
            build_text(x if isinstance(x, str) else (x.get("description", "")))
            for x in instances
        ]
        assert self._model is not None  # set in load(), which raises otherwise
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
