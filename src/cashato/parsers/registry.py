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
    or the surname first (see ``base.GIVEN_FIRST`` / ``base.FAMILY_FIRST``);
  - ``extract_accounts(path) -> list[base.AccountInfo]`` — what the document says
    about the accounts it covers (bank, product, joint/individual, IBAN);
  - ``extract_balances(path) -> list[base.BalanceAnchor]`` — the balances the
    document itself declares (per-row running balance, opening/closing lines),
    which reconciliation checks the parsed movements against.

Adding a bank = drop in a module exposing those two names; nothing here changes
(the module name IS the source id). The package's non-adapter helpers are skipped
by name. Detection markers and currency are parser-coupled knowledge, so they
live WITH the parser.
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
#: source -> account-metadata extractor (optional).
ACCOUNT_EXTRACTORS: dict[str, Callable[..., list]] = {}
#: source -> statement-declared balance extractor (optional).
BALANCE_EXTRACTORS: dict[str, Callable[..., list]] = {}

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
        if hasattr(_mod, "extract_accounts"):
            ACCOUNT_EXTRACTORS[_info.name] = _mod.extract_accounts
        if hasattr(_mod, "extract_balances"):
            BALANCE_EXTRACTORS[_info.name] = _mod.extract_balances

# Ordered list of supported source identifiers (single source of truth).
SOURCE_NAMES: list[str] = list(ADAPTERS)


def detection_signatures() -> list[tuple[str, list[list[str]]]]:
    """(source, marker-groups) for content-based detection, in discovery order."""
    return [(name, _DETECTION[name]) for name in SOURCE_NAMES]
