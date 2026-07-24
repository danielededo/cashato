"""ingest-api — receives a statement upload and enqueues an ingestion job.

Flow: POST /api/v1/uploads -> store the file in object storage (MinIO) -> publish
a job on NATS JetStream (subject ``ingest.jobs``) carrying the object key -> the
etl-worker consumes it. The service stays **lightweight** and **stateless**: it
parses nothing, loads no models, and keeps no local files.

Path conventions: probes at root (``/healthz``, ``/readyz``); business API under
``/api/v1``; ``ROOT_PATH`` (env) for the gateway prefix. OpenAPI at
``/openapi.json``, Swagger UI at ``/docs``, ReDoc at ``/redoc``.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field
from sqlalchemy import text

from cashato import objstore
from cashato.config import setting
from cashato.db.db import get_engine
from cashato.messaging import SUBJECT_FEEDBACK, SUBJECT_INGEST, connect_jetstream
from cashato.obs import (
    inject_trace_headers,
    setup_logging,
    setup_tracing,
    start_metrics_server,
    tracing_enabled,
)
from cashato.parsers.categorize import Categorizer
from cashato.parsers.registry import SOURCE_NAMES

ROOT_PATH = os.environ.get("ROOT_PATH", "")
_log = setup_logging("ingest-api")
# Valid category codes (for feedback validation); labels/model not needed here.
_CATEGORY_CODES = set(Categorizer.load().categories)
# Upload guards (configurable; ConfigMap in phase C).
_MAX_FILE_BYTES = int(setting("uploads.max_file_bytes", 10 * 1024 * 1024))
_ALLOWED_EXT = {e.lower() for e in setting("uploads.allowed_extensions", [".pdf", ".csv", ".xlsx"])}
_CHUNK = 1 << 20  # 1 MiB streaming read

_TAGS = [
    {"name": "health", "description": "Liveness/readiness probes for Kubernetes."},
    {"name": "ingestion", "description": "Upload statements and inspect ingested files."},
    {"name": "feedback", "description": "User category corrections (active learning)."},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    objstore.ensure_bucket()
    app.state.nc, app.state.js = await connect_jetstream()
    yield
    await app.state.nc.drain()


app = FastAPI(
    title="cashato ingest-api",
    version="0.1.0",
    description="Upload API for bank statements; enqueues ingestion jobs on NATS.",
    root_path=ROOT_PATH,
    openapi_tags=_TAGS,
    license_info={"name": "MIT"},
    lifespan=lifespan,
)


# --- response models ---
class UploadAccepted(BaseModel):
    status: str = Field(examples=["queued"])
    filename: str = Field(examples=["Account statement.pdf"])
    stored_as: str = Field(examples=["a1b2c3d4_Account statement.pdf"])
    source: str | None = Field(default=None, examples=[None, "trade_republic"])


class RawFile(BaseModel):
    source: str
    filename: str
    status: str
    rows_total: int
    rows_new: int
    rows_duplicate: int
    error: str | None = None
    uploaded_at: datetime


class FilesResponse(BaseModel):
    files: list[RawFile]


class FeedbackRequest(BaseModel):
    natural_key: str = Field(
        description="Canonical dedup key of the transaction to recategorize.",
        examples=["9f2c...ab"],
    )
    category: str = Field(
        description="New (corrected) category code.", examples=["groceries"]
    )
    corrected_by: str | None = Field(
        default=None, description="Who made the correction (user id).", examples=["daniele"]
    )


class FeedbackAccepted(BaseModel):
    status: str = Field(examples=["queued"])
    natural_key: str
    category: str


# Prometheus metrics on a dedicated port (:9100), uniform across all services.
# The Instrumentator still records HTTP request metrics into the default registry;
# start_metrics_server serves that registry on :9100 instead of the business port.
Instrumentator().instrument(app)
start_metrics_server()

# Distributed tracing: auto-instrument HTTP handlers + the psycopg driver so
# every request and DB query is a span; trace context then rides the NATS
# message headers to the etl-worker (see inject_trace_headers on publish).
setup_tracing("ingest-api")
if tracing_enabled():
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

    FastAPIInstrumentor.instrument_app(app)
    PsycopgInstrumentor().instrument()


@app.get("/healthz", tags=["health"], summary="Liveness probe")
async def healthz():
    return {"status": "ok"}


@app.get("/readyz", tags=["health"], summary="Readiness probe (checks NATS)")
async def readyz():
    ready = getattr(app.state, "nc", None) is not None and app.state.nc.is_connected
    return JSONResponse({"ready": ready}, status_code=200 if ready else 503)


api = APIRouter(prefix="/api/v1", tags=["ingestion"])


@api.post("/uploads", response_model=UploadAccepted, status_code=202, summary="Upload a statement")
async def create_upload(
    file: UploadFile,
    source: str | None = Form(
        default=None,
        description=f"Optional explicit source override. One of: {SOURCE_NAMES}. "
        f"If omitted, the worker detects it by content.",
    ),
):
    """Store the file and enqueue an ingestion job (processed asynchronously).

    Validates the extension and enforces a per-file size cap server-side (413),
    streaming the body so an oversized upload cannot exhaust memory.
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_EXT:
        raise HTTPException(
            status_code=415,
            detail=f"unsupported file type {suffix!r}. Allowed: {sorted(_ALLOWED_EXT)}",
        )
    # Stream to a local temp file (enforcing the size cap), then hand it off to
    # object storage and drop the temp. The job carries only the object KEY.
    key = f"{uuid.uuid4().hex[:8]}_{file.filename}"
    size = 0
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        try:
            while chunk := await file.read(_CHUNK):
                size += len(chunk)
                if size > _MAX_FILE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"file too large (> {_MAX_FILE_BYTES} bytes)",
                    )
                tmp.write(chunk)
            tmp.flush()
            objstore.fput(key, tmp.name)
        finally:
            os.unlink(tmp.name)
    job = {"key": key, "filename": file.filename, "source": source}
    await app.state.js.publish(
        SUBJECT_INGEST, json.dumps(job).encode(), headers=inject_trace_headers()
    )
    return UploadAccepted(
        status="queued", filename=file.filename or "", stored_as=key, source=source
    )


@api.post(
    "/feedback",
    response_model=FeedbackAccepted,
    status_code=202,
    tags=["feedback"],
    summary="Correct a transaction's category",
)
async def submit_feedback(req: FeedbackRequest):
    """Record a user category correction (active learning).

    Validates the category code, then publishes a ``category.feedback`` event.
    The consumer applies it to ``silver.transactions`` (source ``manual``,
    confidence 1.0) and stores it in ``gold.category_feedback`` to feed the next
    model retrain. Applied asynchronously (optimistic UI on the client).
    """
    if req.category not in _CATEGORY_CODES:
        raise HTTPException(
            status_code=422,
            detail=f"unknown category code: {req.category!r}. Valid: {sorted(_CATEGORY_CODES)}",
        )
    event = req.model_dump()
    await app.state.js.publish(
        SUBJECT_FEEDBACK, json.dumps(event).encode(), headers=inject_trace_headers()
    )
    return FeedbackAccepted(status="queued", natural_key=req.natural_key, category=req.category)


@api.get("/files", response_model=FilesResponse, summary="Recently ingested files")
async def list_files():
    """Status of the most recently ingested files (visibility)."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT source, filename, status, rows_total, rows_new, "
                    "rows_total - rows_new AS rows_duplicate, error, uploaded_at "
                    "FROM bronze.raw_files ORDER BY uploaded_at DESC LIMIT 50"
                )
            )
            .mappings()
            .all()
        )
    return {"files": [dict(r) for r in rows]}


app.include_router(api)
