"""MLOps — Retrain the categorization model and register it in MLflow.

Provider-agnostic dataset of **canonical** labels, read from CNPG:
- ``gold.training_labels`` (LLM / manual) — primary signal;
- ``gold.category_feedback`` (user corrections from the frontend) — highest value;
- optionally rows already resolved by rules/MCC as weak anchors (``--include-rules``).

Features are semantic embeddings (``ml/model.EmbeddingKNN``), robust to noise and
multilingual. No TF-IDF, no regex cleaning.

**Champion / challenger (``--register``):** the freshly trained model is a
*challenger*. It is scored on a holdout together with the current ``@champion``
(re-evaluated live on the SAME holdout), a new version is registered, and with
``--promote if-better`` the alias moves only if the challenger is at least as good
— so a bad retrain never regresses what KServe serves. Note the comparison is
mildly conservative: the champion may have seen some holdout rows in its own past
training, biasing toward keeping it (a safe default).

Usage (in-cluster Job / CronJob):
    python ml/train.py --include-rules --stamp "$STAMP" --register --promote if-better
"""

from __future__ import annotations

import argparse
from collections import Counter

from sklearn.metrics import f1_score
from sklearn.model_selection import train_test_split
from sqlalchemy import text

from cashato.config import MODEL_DIR
from cashato.db.db import get_engine
from cashato.ml.model import DEFAULT_K, EmbeddingKNN
from cashato.parsers.categorize import Categorizer, build_text

MODELS_DIR = MODEL_DIR


def load_dataset(include_rules: bool) -> tuple[list[str], list[str]]:
    # The default code is a VERDICT, not a class: an example labeled "other"
    # says "nobody knew", which is no evidence at all — yet as anchors those
    # examples flood the kNN neighborhood (sum-voting) and outvote exact
    # matches of real classes. Unknown stays what the resolver produces when
    # confidence is low, never something the model asserts.
    default = Categorizer.load().default
    engine = get_engine()
    seen: dict[str, str] = {}
    with engine.connect() as conn:
        if include_rules:
            for descr, src, cat in conn.execute(
                text(
                    "SELECT description, source, category FROM silver.transactions "
                    "WHERE category_source IN ('rule','mcc')"
                )
            ):
                seen[build_text(descr, src)] = cat
        # Deterministic precedence: manual labels beat llm/native for the same
        # text (last write into `seen` wins), instead of heap-scan luck.
        for t, cat in conn.execute(
            text(
                "SELECT text_norm, category FROM gold.training_labels "
                "ORDER BY CASE source WHEN 'manual' THEN 2 ELSE 1 END, id"
            )
        ):
            seen[t] = cat
        # Latest correction per natural_key wins — same contract as the
        # loader's reapply; an unordered scan could train on a superseded fix.
        for cat, descr, src in conn.execute(
            text(
                "SELECT f.category, s.description, s.source FROM ("
                "  SELECT DISTINCT ON (natural_key) natural_key, category"
                "  FROM gold.category_feedback ORDER BY natural_key, id DESC"
                ") f JOIN silver.transactions s ON s.natural_key = f.natural_key"
            )
        ):
            seen[build_text(descr, src)] = cat
    seen.pop("", None)
    seen = {t: c for t, c in seen.items() if c != default}
    return list(seen.keys()), list(seen.values())


def _macro_f1(model: EmbeddingKNN, x: list[str], y: list[str]) -> tuple[float, float]:
    """Return (accuracy, macro-F1) of ``model`` on (x, y)."""
    preds = [(p or ("other", 0.0))[0] for p in model.predict_batch(x)]
    acc = sum(a == b for a, b in zip(preds, y, strict=True)) / len(y)
    return acc, f1_score(y, preds, average="macro", zero_division=0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-rules", action="store_true")
    ap.add_argument("--stamp", required=True)
    ap.add_argument("--k", type=int, default=DEFAULT_K)
    ap.add_argument("--min-per-class", type=int, default=3)
    ap.add_argument("--register", action="store_true", help="register a version in MLflow")
    ap.add_argument(
        "--promote",
        choices=["none", "always", "if-better"],
        default="if-better",
        help="move the @champion alias to the new version (only with --register)",
    )
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
    print("Embedding + fit challenger on train split ...")
    challenger = EmbeddingKNN(k=args.k).fit(xtr, ytr)
    acc, macro_f1 = _macro_f1(challenger, xte, yte)
    print(f"Challenger holdout: accuracy={acc:.3f} macro-F1={macro_f1:.3f}")

    # Champion/challenger: re-evaluate the current champion on the SAME holdout.
    champ_f1: float | None = None
    if args.register or args.promote != "none":
        try:
            from cashato.ml.registry import load_champion

            champ = load_champion()
            if champ is not None:
                _, champ_f1 = _macro_f1(champ, xte, yte)
                print(f"Champion holdout : macro-F1={champ_f1:.3f}")
            else:
                print("No @champion yet (first model).")
        except Exception as exc:  # noqa: BLE001
            print(f"[info] could not load champion ({exc}).")

    print("Refit challenger on the full dataset ...")
    challenger.fit(X, y)
    MODELS_DIR.mkdir(exist_ok=True)
    artifact = MODELS_DIR / f"emb-knn-{args.stamp}.joblib"
    challenger.save(artifact)
    challenger.save(MODELS_DIR / "latest.joblib")
    print(f"Local artifact: {artifact} (+ latest.joblib) | examples={len(X)}")

    if not args.register:
        print("[info] --register not set: model NOT registered in MLflow.")
        return 0

    from cashato.ml.registry import log_and_register, set_champion

    version = log_and_register(
        challenger,
        params={
            "model": "embedding-knn",
            "k": args.k,
            "embed_model": challenger.model_name,
            "n_examples": len(X),
            "include_rules": args.include_rules,
        },
        metrics={"accuracy": float(acc), "macro_f1": float(macro_f1)},
        run_name=args.stamp,
    )
    print(f"Registered cashato-categorizer v{version}.")

    promote = args.promote == "always" or (
        args.promote == "if-better" and (champ_f1 is None or macro_f1 >= champ_f1)
    )
    if promote:
        set_champion(version)
        print(f"Promoted v{version} to @champion.")
    else:
        print(f"Kept current champion (challenger {macro_f1:.3f} < {champ_f1:.3f}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
