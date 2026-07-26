"""Database connection helper, shared by the loader and the services."""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

# Local dev points at the docker-compose Postgres; on K8s (phase C) the URL
# comes from the CNPG-generated Secret.
DEFAULT_URL = "postgresql+psycopg://cashato:cashato@localhost:5432/cashato"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


def get_engine(echo: bool = False) -> Engine:
    return create_engine(database_url(), echo=echo, future=True)
