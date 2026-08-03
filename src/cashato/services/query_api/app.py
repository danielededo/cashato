"""query-api — exposes spending aggregates from the GOLD views.

Categories in the DB are codes; here they are localized (``?lang=it|en``) via the
Categorizer (no ML model needed for labels only).

Path conventions: probes at root (``/healthz``, ``/readyz``); business API under
``/api/v1``; ``ROOT_PATH`` (env) for the gateway prefix. OpenAPI at ``/openapi.json``,
Swagger UI at ``/docs``, ReDoc at ``/redoc``.

This module only assembles the service: observability, health probes, and the
router. Endpoints live in routes.py, response shapes in models.py, shared
state (engine, Categorizer) in deps.py.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy import text

from cashato.obs import (
    setup_logging,
    setup_tracing,
    start_metrics_server,
    tracing_enabled,
)

from .deps import ENGINE
from .routes import api

ROOT_PATH = os.environ.get("ROOT_PATH", "")
_log = setup_logging("query-api")

_TAGS = [
    {"name": "health", "description": "Liveness/readiness probes for Kubernetes."},
    {"name": "analytics", "description": "Spending aggregates from the GOLD layer."},
]

app = FastAPI(
    title="cashato query-api",
    version="0.1.0",
    description="Read API over the unified transactions: per-category and monthly aggregates.",
    root_path=ROOT_PATH,
    openapi_tags=_TAGS,
    license_info={"name": "MIT"},
)

# Prometheus metrics on a dedicated port (:9100), uniform across all services.
Instrumentator().instrument(app)
start_metrics_server()

# Distributed tracing: auto-instrument HTTP handlers + the psycopg driver
# (driver-level, so the module-level engine from deps is still traced).
setup_tracing("query-api")
if tracing_enabled():
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

    FastAPIInstrumentor.instrument_app(app)
    PsycopgInstrumentor().instrument()


@app.get("/healthz", tags=["health"], summary="Liveness probe")
def healthz():
    return {"status": "ok"}


@app.get("/readyz", tags=["health"], summary="Readiness probe (checks the DB)")
def readyz():
    try:
        with ENGINE.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"ready": True}
    except Exception:
        return JSONResponse({"ready": False}, status_code=503)


app.include_router(api)
