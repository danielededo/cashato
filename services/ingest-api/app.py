"""ingest-api — receives a statement upload and enqueues an ingestion job.

Flow: POST /api/v1/uploads -> save the file on a shared volume -> publish a job
on NATS JetStream (subject ``ingest.jobs``) -> the etl-worker consumes it.
The service stays **lightweight**: it parses nothing and loads no models.

Path conventions: probes at root (``/healthz``, ``/readyz``); business API under
``/api/v1``; ``ROOT_PATH`` (env) for the gateway prefix. OpenAPI at
``/openapi.json``, Swagger UI at ``/docs``, ReDoc at ``/redoc``.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import APIRouter, FastAPI, Form, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from prometheus_fastapi_instrumentator import Instrumentator  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from sqlalchemy import text  # noqa: E402

from db.db import get_engine  # noqa: E402
from libs.config import SOURCE_NAMES, setting  # noqa: E402
from libs.messaging import SUBJECT_FEEDBACK, SUBJECT_INGEST, connect_jetstream  # noqa: E402
from libs.obs import setup_logging  # noqa: E402
from libs.parsers.categorize import Categorizer  # noqa: E402

UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "data/uploads"))
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
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
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


# Prometheus HTTP metrics at /metrics
Instrumentator().instrument(app).expose(app, endpoint="/metrics", tags=["health"])


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
    dest = UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{file.filename}"
    size = 0
    with open(dest, "wb") as out:
        while chunk := await file.read(_CHUNK):
            size += len(chunk)
            if size > _MAX_FILE_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"file too large (> {_MAX_FILE_BYTES} bytes)",
                )
            out.write(chunk)
    job = {"path": str(dest), "filename": file.filename, "source": source}
    await app.state.js.publish(SUBJECT_INGEST, json.dumps(job).encode())
    return UploadAccepted(
        status="queued", filename=file.filename, stored_as=dest.name, source=source
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
    await app.state.js.publish(SUBJECT_FEEDBACK, json.dumps(event).encode())
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
