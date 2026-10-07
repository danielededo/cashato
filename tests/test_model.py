"""The kNN vote: confidence is nearest evidence, margin is how contested.

Numpy only: the embedding model is stubbed, the artifact never touched."""

import numpy as np
import pytest

from cashato.ml import label_llm
from cashato.ml.model import EmbeddingKNN, Prediction, _vote


def _knn(labels: list[str], vectors: list[list[float]], k: int = 5) -> EmbeddingKNN:
    m = EmbeddingKNN("stub", k)
    m._labels = labels
    m._vectors = np.asarray(vectors, dtype=np.float32)
    return m


class TestVote:
    def test_unanimous_neighbors_give_full_margin(self):
        p = _vote(np.array([0.9, 0.8, 0.7]), ["dining"] * 3, k=5)
        assert p == Prediction("dining", 0.9, 1.0)

    def test_margin_is_the_winners_lead_as_a_share_of_the_vote(self):
        # 3-2 split on even similarities: (3s - 2s) / 5s
        p = _vote(np.array([0.8] * 5), ["a", "a", "a", "b", "b"], k=5)
        assert p.code == "a"
        assert p.margin == pytest.approx(0.2)
        p = _vote(np.array([0.8] * 5), ["a", "a", "a", "a", "b"], k=5)
        assert p.margin == pytest.approx(0.6)

    def test_confidence_is_evidence_for_the_winner_not_the_top1(self):
        # top-1 neighbor belongs to the losing label: confidence must not be 0.95
        sims = np.array([0.95, 0.80, 0.79, 0.78, 0.1])
        p = _vote(sims, ["b", "a", "a", "a", "a"], k=5)
        assert p.code == "a"
        assert p.confidence == pytest.approx(0.80)
        assert 0 < p.margin < 1

    def test_only_k_nearest_vote(self):
        sims = np.array([0.9, 0.2, 0.2, 0.2])
        p = _vote(sims, ["a", "b", "b", "b"], k=1)
        assert p == Prediction("a", pytest.approx(0.9), 1.0)

    def test_prediction_still_indexes_like_the_old_pair(self):
        # Categorizer / train / predictor read pred[0], pred[1]
        p = Prediction("a", 0.8, 0.5)
        assert (p[0], p[1]) == ("a", 0.8)


class TestPredictBatch:
    def test_batch_matches_one_by_one(self, monkeypatch):
        m = _knn(["a", "a", "b"], [[1, 0], [0.9, 0.1], [0, 1]], k=3)
        monkeypatch.setattr(
            m, "_encode", lambda texts: np.asarray([[1, 0]] * len(texts), np.float32)
        )
        one = m.predict_one("x")
        batch = m.predict_batch(["x", "x"])
        assert batch == [one, one]
        assert one is not None and one.code == "a"

    def test_empty_model_predicts_nothing(self):
        m = EmbeddingKNN("stub")
        assert m.predict_one("x") is None
        assert m.predict_batch(["x"]) == [None]


class TestUncertainRows:
    class _Stub:
        def __init__(self, preds):
            self._preds = preds

        def predict_batch(self, texts):
            assert len(texts) == len(self._preds)
            return self._preds

    def test_contested_rows_only_most_contested_first(self):
        rows = [
            ("sure", "intesa"),
            ("split", "intesa"),
            ("coin flip", "revolut"),
            ("none", "intesa"),
        ]
        stub = self._Stub(
            [
                Prediction("a", 0.9, 1.0),
                Prediction("a", 0.8, 0.4),
                Prediction("b", 0.85, 0.1),
                None,
            ]
        )
        out = label_llm.uncertain_rows(stub, rows, margin=0.5)
        assert [(d, s) for d, s, _ in out] == [("coin flip", "revolut"), ("split", "intesa")]
        assert [m for _, _, m in out] == [pytest.approx(0.1), pytest.approx(0.4)]

    def test_empty_input_short_circuits(self):
        assert label_llm.uncertain_rows(self._Stub([]), [], margin=0.5) == []
