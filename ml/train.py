"""M2 — Build the categorization index (embeddings + kNN).

Provider-agnostic dataset of **canonical** labels:
- ``gold.training_labels`` (LLM / manual) — primary signal;
- ``gold.category_feedback`` (user corrections) — highest priority;
- optionally rows already resolved by rules/MCC as weak anchors (``--include-rules``).

Features are semantic embeddings (``ml/model.EmbeddingKNN``), robust to noise and
multilingual. No TF-IDF, no regex cleaning. Holdout metrics; MLflow tracking if
available.

Usage:
    ./.venv/bin/python ml/train.py --include-rules --stamp "$(date +%Y%m%d-%H%M)"
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from sklearn.metrics import f1_score
from sklearn.model_selection import train_test_split
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.db import get_engine  # noqa: E402
from libs.parsers.categorize import build_text  # noqa: E402
from ml.model import DEFAULT_K, EmbeddingKNN  # noqa: E402

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"


def load_dataset(include_rules: bool) -> tuple[list[str], list[str]]:
    engine = get_engine()
    seen: dict[str, str] = {}
    with engine.connect() as conn:
        if include_rules:
            for descr, cat in conn.execute(
                text(
                    "SELECT description, category FROM silver.transactions "
                    "WHERE category_source IN ('rule','mcc')"
                )
            ):
                seen[build_text(descr)] = cat
        for t, cat in conn.execute(text("SELECT text_norm, category FROM gold.training_labels")):
            seen[t] = cat
        for cat, descr in conn.execute(
            text(
                "SELECT f.category, s.description FROM gold.category_feedback f "
                "JOIN silver.transactions s ON s.natural_key = f.natural_key"
            )
        ):
            seen[build_text(descr)] = cat
    seen.pop("", None)
    return list(seen.keys()), list(seen.values())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-rules", action="store_true")
    ap.add_argument("--stamp", required=True)
    ap.add_argument("--k", type=int, default=DEFAULT_K)
    ap.add_argument("--min-per-class", type=int, default=3)
    args = ap.parse_args()

    X, y = load_dataset(args.include_rules)
    counts = Counter(y)
    keep = [(xi, yi) for xi, yi in zip(X, y, strict=True) if counts[yi] >= args.min_per_class]
    X, y = [k[0] for k in keep], [k[1] for k in keep]
    print(f"Dataset: {len(X)} examples, {len(set(y))} classes -> {dict(Counter(y))}")
    if len(set(y)) < 2:
        print("[ERROR] need >=2 classes with enough examples (label with ml/label_llm.py)")
        return 1

    xtr, xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    print("Embedding + fit on train ...")
    model = EmbeddingKNN(k=args.k).fit(xtr, ytr)
    ypred = [(model.predict_one(x) or ("other", 0.0))[0] for x in xte]
    acc = sum(a == b for a, b in zip(ypred, yte, strict=True)) / len(yte)
    macro_f1 = f1_score(yte, ypred, average="macro", zero_division=0)
    print(f"Holdout: accuracy={acc:.3f} macro-F1={macro_f1:.3f}")

    print("Refit on the full dataset ...")
    model.fit(X, y)
    MODELS_DIR.mkdir(exist_ok=True)
    artifact = MODELS_DIR / f"emb-knn-{args.stamp}.joblib"
    model.save(artifact)
    model.save(MODELS_DIR / "latest.joblib")
    print(f"Index saved: {artifact} (+ latest.joblib) | examples={len(X)}")

    try:
        import mlflow

        mlflow.set_experiment("cashato-categorizer")
        with mlflow.start_run(run_name=args.stamp):
            mlflow.log_params(
                {
                    "model": "embedding-knn",
                    "k": args.k,
                    "embed_model": model.model_name,
                    "n_examples": len(X),
                }
            )
            mlflow.log_metrics({"accuracy": float(acc), "macro_f1": float(macro_f1)})
            mlflow.log_artifact(str(artifact))
        print("Metrics logged to MLflow.")
    except Exception as exc:  # noqa: BLE001
        print(f"[info] MLflow not used ({exc}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
