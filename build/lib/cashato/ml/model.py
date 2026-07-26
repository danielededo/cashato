"""Category classifier based on **semantic embeddings + kNN**.

Provider-agnostic and multilingual: the description is turned into a semantic
vector by a local `sentence-transformers` model; the category is that of the
most similar labeled example (from the LLM / rules / corrections). Robust to
noise (dates, codes, masked card numbers) and able to generalize to unseen
merchants and languages -- with no regex cleaning.

Interface used by the `Categorizer`: ``predict_one(text) -> (code, confidence)``.
The artifact stores only model-name + vectors + labels; the embedding model is
loaded lazily (sentence-transformers cache).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import numpy as np

from cashato.config import setting

DEFAULT_MODEL = setting(
    "categorization.embed_model",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
DEFAULT_K = int(setting("categorization.knn_k", 5))


class EmbeddingKNN:
    def __init__(self, model_name: str = DEFAULT_MODEL, k: int = DEFAULT_K):
        self.model_name = model_name
        self.k = k
        self._vectors: np.ndarray | None = None
        self._labels: list[str] | None = None
        self._st: Any = None  # SentenceTransformer, lazy

    # --- embedding (lazy model load) ---
    def _encode(self, texts: list[str]) -> np.ndarray:
        if self._st is None:
            from sentence_transformers import SentenceTransformer

            self._st = SentenceTransformer(self.model_name)
        vecs = self._st.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vecs, dtype=np.float32)

    # --- fit / predict ---
    def fit(self, texts: list[str], labels: list[str]) -> EmbeddingKNN:
        self._vectors = self._encode(texts)
        self._labels = list(labels)
        return self

    def predict_one(self, text: str) -> tuple[str, float] | None:
        if self._vectors is None or not self._labels:
            return None
        q = self._encode([text])[0]
        sims = self._vectors @ q  # cosine (normalized vectors)
        # weighted vote of the k nearest neighbors
        idx = np.argsort(-sims)[: self.k]
        scores: dict[str, float] = {}
        for i in idx:
            scores[self._labels[i]] = scores.get(self._labels[i], 0.0) + float(sims[i])
        best = max(scores, key=lambda kk: scores[kk])
        # confidence = similarity of the best neighbor (0..1)
        conf = float(sims[idx[0]])
        return best, conf

    def predict_batch(self, texts: list[str]) -> list[tuple[str, float] | None]:
        """Like predict_one but vectorized: a SINGLE encode for all queries
        (much faster than the row-by-row call)."""
        texts = list(texts)
        if self._vectors is None or not self._labels or not texts:
            return [None] * len(texts)
        q = self._encode(texts)  # (N, d)
        sims = q @ self._vectors.T  # (N, M)
        out: list[tuple[str, float] | None] = []
        for row in sims:
            idx = np.argsort(-row)[: self.k]
            scores: dict[str, float] = {}
            for i in idx:
                scores[self._labels[i]] = scores.get(self._labels[i], 0.0) + float(row[i])
            best = max(scores, key=lambda kk: scores[kk])
            out.append((best, float(row[idx[0]])))
        return out

    # --- persistence (lightweight artifact: vectors + labels + model name) ---
    def save(self, path: str | Path) -> None:
        joblib.dump(
            {
                "model_name": self.model_name,
                "k": self.k,
                "vectors": self._vectors,
                "labels": self._labels,
            },
            path,
        )

    @classmethod
    def load(cls, path: str | Path) -> EmbeddingKNN:
        d = joblib.load(path)
        m = cls(d["model_name"], d.get("k", 5))
        m._vectors = d["vectors"]
        m._labels = d["labels"]
        return m
