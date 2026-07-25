"""Adapter registry: auto-discovers source parsers in this package.

Each source is a module ``cashato/parsers/<name>.py`` exposing:
  - ``parse(path) -> list[Transaction]`` — the adapter;
  - ``DETECTION: list[list[str]]`` — content-detection marker groups. A file
    matches the source if, for ANY group, ALL its markers appear in the file's
    lowercased head text (see ``detect.py``).

and, optionally:
  - ``extract_holder(path) -> str | None`` — the account holder off the document
    header (``None`` when the format has none, e.g. CSV exports);
  - ``NAME_ORDER`` — whether that source's documents write the given name first
    or the surname first (see ``base.GIVEN_FIRST`` / ``base.FAMILY_FIRST``).

Adding a bank = drop in a module exposing those two names; nothing here changes
(the module name IS the source id). The package's non-adapter helpers are skipped
by name. This replaces the old ``config/sources.yaml`` registry: detection markers
and currency are parser-coupled knowledge, so they live WITH the parser.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable

from . import __name__ as _PKG
from . import __path__ as _PKG_PATH

# Modules in this package that are NOT source adapters.
_NON_ADAPTERS = {"base", "categorize", "detect", "registry"}

ADAPTERS: dict[str, Callable] = {}
_DETECTION: dict[str, list[list[str]]] = {}
#: source -> holder extractor, only for adapters that implement one (optional).
HOLDER_EXTRACTORS: dict[str, Callable[..., str | None]] = {}
#: source -> name-order convention of that source's documents (optional).
NAME_ORDERS: dict[str, str] = {}

for _info in sorted(pkgutil.iter_modules(_PKG_PATH), key=lambda m: m.name):
    if _info.ispkg or _info.name in _NON_ADAPTERS:
        continue
    _mod = importlib.import_module(f"{_PKG}.{_info.name}")
    if hasattr(_mod, "parse") and hasattr(_mod, "DETECTION"):
        ADAPTERS[_info.name] = _mod.parse
        _DETECTION[_info.name] = _mod.DETECTION
        if hasattr(_mod, "extract_holder"):
            HOLDER_EXTRACTORS[_info.name] = _mod.extract_holder
        if hasattr(_mod, "NAME_ORDER"):
            NAME_ORDERS[_info.name] = _mod.NAME_ORDER

# Ordered list of supported source identifiers (single source of truth).
SOURCE_NAMES: list[str] = list(ADAPTERS)


def detection_signatures() -> list[tuple[str, list[list[str]]]]:
    """(source, marker-groups) for content-based detection, in discovery order."""
    return [(name, _DETECTION[name]) for name in SOURCE_NAMES]
