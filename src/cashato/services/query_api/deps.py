"""Shared per-process state: one engine, one Categorizer, one row helper.

Split out of app.py so the routes and the health probes import the same
instances without importing each other.
"""

from __future__ import annotations

from fastapi import Query
from sqlalchemy import text

from cashato.db.db import get_engine
from cashato.parsers.categorize import Categorizer

CAT = Categorizer.load()
ENGINE = get_engine()

LANG = Query(default="it", description="Category label language", examples=["it", "en"])


def fetch_rows(sql: str, params: dict | None = None) -> list[dict]:
    with ENGINE.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params or {}).mappings().all()]
