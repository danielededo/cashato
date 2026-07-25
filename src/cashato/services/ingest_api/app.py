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
from typing import Literal

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
from cashato.parsers.base import GIVEN_FIRST, format_holder, given_name
from cashato.parsers.categorize import Categorizer
from cashato.parsers.registry import NAME_ORDERS, SOURCE_NAMES

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
    {"name": "profile", "description": "Who the ingested statements belong to."},
    {"name": "admin", "description": "Operational: reprocess stored files, reset data."},
]

# Tables cleared by a reset. "data" keeps the learned labels (active-learning
# memory); "all" also wipes them for a true from-scratch restart.
_RESET_TABLES = {
    "data": ["silver.transactions", "silver.accounts", "bronze.raw_files"],
    "all": [
        "silver.transactions",
        "silver.accounts",
        "bronze.raw_files",
        "gold.training_labels",
        "gold.category_feedback",
    ],
}


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
    # Only statement PDFs name the holder; exports legitimately leave this empty.
    account_holder: str | None = None


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


class Profile(BaseModel):
    """The account holder, as read off the ingested statements. All optional:
    CSV/XLSX exports carry no addressee, so "unknown" is a normal state."""

    display_name: str | None = Field(default=None, examples=["Mario Rossi"])
    given_name: str | None = Field(default=None, examples=["Daniele"])
    source: str | None = Field(default=None, description="Source the name was read from.")
    variants: list[str] = Field(
        default_factory=list, description="Distinct holder spellings seen across sources."
    )


class ResetRequest(BaseModel):
    scope: Literal["data", "all"] = Field(
        default="data",
        description="'data' wipes transactions+files but KEEPS learned labels; "
        "'all' also wipes gold.training_labels + gold.category_feedback.",
    )


class AdminResult(BaseModel):
    status: str = Field(examples=["ok", "queued"])
    detail: str
    count: int | None = None


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
                    "rows_total - rows_new AS rows_duplicate, error, uploaded_at, "
                    "account_holder "
                    "FROM bronze.raw_files ORDER BY uploaded_at DESC LIMIT 50"
                )
            )
            .mappings()
            .all()
        )
    return {"files": [dict(r) for r in rows]}


@api.get("/profile", response_model=Profile, tags=["profile"], summary="Account holder")
async def profile():
    """Who the ingested statements belong to, for a personalized home page.

    The holder is read off the statement headers at ingestion time; CSV/XLSX
    exports carry none. Sources disagree on name order (Revolut writes "DANIELE
    ROSSI", Italian statements "ROSSI MARIO"), so the greeting
    name is derived from the *declared* convention of the source that supplied
    the name rather than guessed from the string. Everything is nullable: an
    empty profile is a normal state (no PDF ingested yet).
    """
    engine = get_engine()
    with engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT account_holder, source, COUNT(*) AS n "
                    "FROM bronze.raw_files "
                    "WHERE account_holder IS NOT NULL AND account_holder <> '' "
                    "GROUP BY account_holder, source "
                    "ORDER BY n DESC, MAX(uploaded_at) DESC"
                )
            )
            .mappings()
            .all()
        )
    if not rows:
        return Profile()
    top = rows[0]
    order = NAME_ORDERS.get(top["source"], GIVEN_FIRST)
    return Profile(
        display_name=format_holder(top["account_holder"]),
        given_name=given_name(top["account_holder"], order) or None,
        source=top["source"],
        # Distinct spellings seen across sources — transparency, and a hint that
        # statements from more than one holder were mixed in.
        variants=sorted({format_holder(r["account_holder"]) for r in rows}),
    )


@api.post(
    "/admin/reprocess",
    response_model=AdminResult,
    status_code=202,
    tags=["admin"],
    summary="Re-enqueue the ETL over all stored files",
)
async def reprocess():
    """Re-run ingestion over every retained object (by key), no re-upload needed.

    Idempotent: dedup by ``natural_key`` means already-reconciled rows are skipped,
    so this safely re-parses (e.g. after a parser fix or a model retrain).
    """
    keys = objstore.list_keys()
    for key in keys:
        # key is "<uuid8>_<original filename>"; recover the filename for logging/detect.
        filename = key.split("_", 1)[1] if "_" in key else key
        # force: the whole point is to re-parse files already marked 'parsed'
        # (after a parser fix, a model retrain, or a new column to backfill);
        # without it the loader stops at the sha256 check and does nothing.
        job = {"key": key, "filename": filename, "source": None, "force": True}
        await app.state.js.publish(
            SUBJECT_INGEST, json.dumps(job).encode(), headers=inject_trace_headers()
        )
    _log.info("reprocess enqueued", extra={"fields": {"count": len(keys)}})
    return AdminResult(status="queued", detail=f"re-enqueued {len(keys)} file(s)", count=len(keys))


@api.post(
    "/admin/reset",
    response_model=AdminResult,
    tags=["admin"],
    summary="Delete ingested data (destructive)",
)
async def reset(req: ResetRequest):
    """Truncate the ingested data and drop the stored files. Destructive.

    ``scope=data`` keeps the active-learning memory (``gold.training_labels`` +
    ``gold.category_feedback``); ``scope=all`` wipes those too. Also clears the
    object bucket so a later reprocess does not repopulate.
    """
    tables = _RESET_TABLES[req.scope]
    engine = get_engine()
    with engine.begin() as conn:
        for tbl in tables:
            conn.execute(text(f"TRUNCATE TABLE {tbl} RESTART IDENTITY CASCADE"))
    removed = objstore.clear()
    _log.warning(
        "data reset", extra={"fields": {"scope": req.scope, "tables": tables, "files_removed": removed}}
    )
    return AdminResult(
        status="ok",
        detail=f"reset (scope={req.scope}): cleared {len(tables)} table(s) and {removed} stored file(s)",
        count=removed,
    )


app.include_router(api)
