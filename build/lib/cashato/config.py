"""Central configuration loader (leaf module: no adapter/model imports).

Reads tunable settings from ``settings.yaml``; ``categorie.yaml`` / ``mcc.yaml``
are read by the categorizer. The source registry + content-detection signatures
now live WITH the parsers (``cashato.parsers.registry``), not here.

The config directory is resolved from ``CASHATO_CONFIG_DIR`` — a read-only mounted
ConfigMap in the cluster; defaults to ``./config`` for local dev. Model artifacts
likewise resolve from ``CASHATO_MODEL_DIR`` (default ``./models``).
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
    """Read a dotted setting from settings.yaml, e.g. ``setting('categorization.model_threshold')``."""
    node: Any = _load("settings.yaml")
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node
