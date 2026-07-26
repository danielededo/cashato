"""Database connection helper, shared by the loader and the services."""

from __future__ import annotations

import os
from functools import cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

# Local dev points at the docker-compose Postgres; on K8s (phase C) the URL
# comes from the CNPG-generated Secret.
DEFAULT_URL = "postgresql+psycopg://cashato:cashato@localhost:5432/cashato"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


@cache
def _engine(url: str, echo: bool) -> Engine:
    return create_engine(url, echo=echo, future=True)


def get_engine(echo: bool = False) -> Engine:
    """One Engine (= one connection pool) per URL for the process lifetime.

    This used to build a NEW engine per call: every /files poll opened a fresh
    Postgres connection whose pooled socket only closed on garbage collection.
    Memoized here so all callers share the pool without each caching it.
    """
    return _engine(database_url(), echo)
