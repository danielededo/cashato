"""Category classifier based on **semantic embeddings + kNN**.

Provider-agnostic and multilingual: the description is turned into a semantic
vector by a local `sentence-transformers` model; the category is that of the
most similar labeled example (from the LLM / rules / corrections). Robust to
noise (dates, codes, masked card numbers) and able to generalize to unseen
merchants and languages -- with no regex cleaning.

Interface used by the `Categorizer`: ``predict_one(text) -> Prediction``, a
``(code, confidence, margin)`` tuple — callers that only index ``[0]``/``[1]``
keep working. ``confidence`` gates the resolver chain; ``margin`` is the
uncertainty signal the offline labeler reads (see ``label_llm``).
The artifact stores only model-name + vectors + labels; the embedding model is
loaded lazily (sentence-transformers cache).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple

import numpy as np

from cashato.config import setting

DEFAULT_MODEL = setting(
    "categorization.embed_model",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
DEFAULT_K = int(setting("categorization.knn_k", 5))


class Prediction(NamedTuple):
    """One kNN verdict.

    ``confidence`` is the best similarity AMONG THE WINNING LABEL's neighbors:
    the top-1 neighbor can belong to a label the vote rejected, and reporting
    its similarity gated the threshold on evidence for the wrong class.

    ``margin`` is the winner's lead in the weighted vote as a share of the
    whole vote, ``(s1 - s2) / sum(s)`` in ``[0, 1]``: 1.0 when every neighbor
    agrees, ~0.6 for a 4-1 split, ~0.2 for 3-2. Confidence says how close the
    nearest evidence is; margin says how contested it is — a row can sit well
    above the threshold and still be a coin flip between two classes, and
    those are the rows worth a second opinion.
    """

    code: str
    confidence: float
    margin: float


def _vote(sims: np.ndarray, labels: list[str], k: int) -> Prediction:
    """Weighted vote of the ``k`` nearest neighbors of one query."""
    idx = np.argsort(-sims)[:k]
    scores: dict[str, float] = {}
    for i in idx:
        scores[labels[i]] = scores.get(labels[i], 0.0) + float(sims[i])
    ranked = sorted(scores.values(), reverse=True)
    best = max(scores, key=lambda kk: scores[kk])
    conf = max(float(sims[i]) for i in idx if labels[i] == best)
    total = sum(max(s, 0.0) for s in ranked)
    runner_up = ranked[1] if len(ranked) > 1 else 0.0
    margin = (ranked[0] - runner_up) / total if total > 0 else 0.0
    return Prediction(best, conf, max(0.0, min(1.0, margin)))


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

    def predict_one(self, text: str) -> Prediction | None:
        if self._vectors is None or not self._labels:
            return None
        q = self._encode([text])[0]
        return _vote(self._vectors @ q, self._labels, self.k)  # cosine (normalized vectors)

    def predict_batch(self, texts: list[str]) -> list[Prediction | None]:
        """Like predict_one but vectorized: a SINGLE encode for all queries
        (much faster than the row-by-row call)."""
        texts = list(texts)
        if self._vectors is None or not self._labels or not texts:
            return [None] * len(texts)
        q = self._encode(texts)  # (N, d)
        sims = q @ self._vectors.T  # (N, M)
        return [_vote(row, self._labels, self.k) for row in sims]

    # --- persistence (lightweight artifact: vectors + labels + model name) ---
    # joblib is imported here, not at module top, so the predict path (and the
    # unit tests exercising it) need only numpy, which the svc extra already
    # carries; joblib ships with the train/predict extras.
    def save(self, path: str | Path) -> None:
        import joblib

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
        import joblib

        d = joblib.load(path)
        m = cls(d["model_name"], d.get("k", 5))
        m._vectors = d["vectors"]
        m._labels = d["labels"]
        return m
