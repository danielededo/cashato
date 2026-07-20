"""Import an existing EmbeddingKNN artifact into the MLflow registry as the
incumbent ``@champion`` (C6 / MLOps).

Used once to **preserve the model we already have** (``models/latest.joblib``,
whose original training dataset is no longer reproducible) without retraining, so
KServe has a versioned ``cashato-categorizer@champion`` to serve from day one.
Future retrains (``ml/train.py``) then compete against it as challengers.

Runs as an in-cluster Job (the artifact is baked into the training image).
Idempotent with ``--if-absent``: does nothing when a ``@champion`` already exists,
so it is safe to re-apply / re-sync (only a full MLflow-DB rebuild re-imports it).

Usage:
    python ml/register_model.py --artifact models/latest.joblib --if-absent
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ml.model import EmbeddingKNN  # noqa: E402
from ml.registry import champion_exists, log_and_register, set_champion  # noqa: E402

DEFAULT_ARTIFACT = Path(__file__).resolve().parents[1] / "models" / "latest.joblib"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact", default=str(DEFAULT_ARTIFACT))
    ap.add_argument("--stamp", default="import")
    ap.add_argument(
        "--if-absent",
        action="store_true",
        help="skip when a @champion already exists (safe to re-run)",
    )
    args = ap.parse_args()

    if args.if_absent and champion_exists():
        print("A @champion already exists — nothing to import.")
        return 0

    path = Path(args.artifact)
    if not path.exists():
        print(f"[ERROR] artifact not found: {path}")
        return 1

    model = EmbeddingKNN.load(path)
    n = len(model._labels or [])
    print(f"Loaded {path.name}: {n} examples, embed_model={model.model_name}")

    version = log_and_register(
        model,
        params={
            "model": "embedding-knn",
            "source": "imported",
            "k": model.k,
            "embed_model": model.model_name,
            "n_examples": n,
        },
        metrics={},
        run_name=f"import-{args.stamp}",
    )
    set_champion(version)
    print(f"Imported as cashato-categorizer v{version} and set @champion.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
