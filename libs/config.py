"""Central configuration loader (leaf module: no adapter/model imports).

Single source of truth derived from ``config/*.yaml``:
- ``SOURCE_NAMES`` and per-source metadata (from ``sources.yaml``);
- content-detection signatures;
- tunable settings (from ``settings.yaml``).

In phase C the YAML files become Kubernetes ConfigMaps; this loader is unchanged.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


@cache
def _load(name: str) -> dict:
    path = _CONFIG_DIR / name
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _sources() -> dict[str, dict]:
    return _load("sources.yaml").get("sources", {})


# Ordered list of supported source identifiers (single source of truth).
SOURCE_NAMES: list[str] = list(_sources())


def source_meta(name: str) -> dict:
    return _sources().get(name, {})


def source_currency(name: str, default: str = "EUR") -> str:
    return source_meta(name).get("currency", default)


def detection_signatures() -> list[tuple[str, list[list[str]]]]:
    """(source, marker-groups) in declared order, for content-based detection."""
    return [(name, spec.get("detection", [])) for name, spec in _sources().items()]


def setting(path: str, default: Any = None) -> Any:
    """Read a dotted setting from settings.yaml, e.g. ``setting('categorization.model_threshold')``."""
    node: Any = _load("settings.yaml")
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node
