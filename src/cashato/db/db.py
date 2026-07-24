"""Helper di connessione al database, condiviso da loader e servizi."""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

# In dev locale punta al Postgres di docker-compose; in K8s (Fase C) la URL
# arriva dal Secret generato da CNPG.
DEFAULT_URL = "postgresql+psycopg://cashato:cashato@localhost:5432/cashato"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


def get_engine(echo: bool = False) -> Engine:
    return create_engine(database_url(), echo=echo, future=True)
