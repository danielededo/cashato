"""Adapter registry: wires each source name to its ``parse`` callable.

For every source in ``config/sources.yaml`` there is a module
``libs/parsers/<name>.py`` exposing ``parse(path)`` (auto-discovered by name).
Adding a bank = add a config entry + a module of the same name (fork/monorepo
model); no code change here. See CONTRIBUTING.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable

from libs.config import SOURCE_NAMES as _BUILTIN_NAMES
from libs.config import detection_signatures as _builtin_detection

# adapters: module name == source name (from config/sources.yaml)
ADAPTERS: dict[str, Callable] = {
    name: importlib.import_module(f"libs.parsers.{name}").parse for name in _BUILTIN_NAMES
}

SOURCE_NAMES: list[str] = list(ADAPTERS)


def detection_signatures() -> list[tuple[str, list[list[str]]]]:
    """Content-detection signatures per source, from config."""
    return list(_builtin_detection())
