"""Central configuration loader (leaf module: no adapter/model imports).

Reads tunable settings from ``settings.yaml``; ``categories.yaml`` / ``mcc.yaml``
are read by the categorizer. The source registry and detection signatures live
in ``cashato.parsers.registry``.

The config directory is resolved from ``CASHATO_CONFIG_DIR`` — a read-only mounted
ConfigMap in the cluster; defaults to ``./config`` for local dev. Model artifacts
likewise resolve from ``CASHATO_MODEL_DIR`` (default ``./models``).

Values are cached for the life of the process (``@cache``). That is safe in the
cluster because the ConfigMap is generated with a kustomize name hash: editing
``config/*.yaml`` produces a NEW ConfigMap name, Argo rewrites the Deployments,
and the pods roll — a process never outlives its config version. Anywhere else
(local scripts, notebooks) an edit needs a process restart to be seen.
"""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(os.environ.get("CASHATO_CONFIG_DIR", "config"))
MODEL_DIR = Path(os.environ.get("CASHATO_MODEL_DIR", "models"))


@cache
def _load(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@cache
def bank_names() -> dict[str, str]:
    """ABI code -> bank name, from ``banks.yaml``.

    Lets an adapter name the bank behind an IBAN when the statement itself does
    not (Intesa's quarterly statement never spells its own name out). Missing
    file or key is not an error — the caller falls back to no bank name.
    """
    try:
        raw = _load("banks.yaml").get("abi") or {}
    except FileNotFoundError:
        return {}
    # YAML may parse an unquoted ABI as an int, losing the leading zero.
    return {str(k).zfill(5): str(v) for k, v in raw.items()}


def setting(path: str, default: Any = None) -> Any:
    """Read a dotted setting from settings.yaml, e.g. ``setting('categorization.model_threshold')``.

    A missing settings.yaml means "all defaults", not a crash — same contract
    as ``bank_names()``.
    """
    try:
        node: Any = _load("settings.yaml")
    except FileNotFoundError:
        return default
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node
