"""HTTP client for the KServe model predictor (C6d).

A drop-in for ``ml.model.EmbeddingKNN`` at inference time: same interface
(``predict_batch`` / ``predict_one``) so the ``Categorizer`` resolver chain can
use the in-cluster model **over HTTP** instead of loading torch locally. This is
what keeps the categorizer service light — the heavy model lives in the KServe
predictor pod. Uses stdlib urllib (no extra service dependency).
"""

from __future__ import annotations

import json
import os
import urllib.request

DEFAULT_URL = os.environ.get(
    "PREDICTOR_URL",
    "http://categorizer-predictor.cashato-ml.svc/v1/models/categorizer:predict",
)


class KServeModel:
    def __init__(self, url: str = DEFAULT_URL, timeout: float = 120.0):
        self.url = url
        self.timeout = timeout

    def predict_batch(self, texts: list[str]) -> list[tuple[str, float] | None]:
        texts = list(texts)
        if not texts:
            return []
        payload = json.dumps({"instances": texts}).encode()
        req = urllib.request.Request(
            self.url, data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = json.loads(resp.read())
        return [(p["category"], float(p["confidence"])) for p in body["predictions"]]

    def predict_one(self, text: str) -> tuple[str, float] | None:
        out = self.predict_batch([text])
        return out[0] if out else None
