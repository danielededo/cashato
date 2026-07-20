"""etl-worker — consumes ingestion jobs from NATS and populates the DB.

For each job: determine the source (explicit override or ``detect_source``),
parse with the right adapter, normalize, dedup and persist (bronze + silver)
with the **fast-path** category (MCC + rules). ML categorization is a separate
concern; the etl-worker stays lightweight (no torch/model).

Observability: structured JSON logs to stdout + Prometheus metrics on
``METRICS_PORT`` (default 9100). The collection stack (Prometheus/Loki/Grafana)
is deployed in phase C.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prometheus_client import Counter, Histogram  # noqa: E402
from sqlalchemy import text  # noqa: E402

import load  # noqa: E402  (reusable loader: parse -> bronze/silver + fast-path)
from db.db import get_engine  # noqa: E402
from libs import objstore  # noqa: E402
from libs.messaging import SUBJECT_FEEDBACK, SUBJECT_INGEST, connect_jetstream  # noqa: E402
from libs.obs import setup_logging, start_metrics_server  # noqa: E402
from libs.parsers.detect import detect_source  # noqa: E402

log = setup_logging("etl-worker")

JOBS = Counter("cashato_etl_jobs_total", "ETL jobs processed", ["status"])
ROWS = Counter("cashato_etl_rows_ingested_total", "New rows inserted into silver")
PROC = Histogram("cashato_etl_process_seconds", "Job processing time (s)")
FEEDBACK = Counter("cashato_etl_feedback_total", "Category corrections applied", ["status"])


def _process(key: str, filename: str | None, source_override: str | None) -> None:
    # Fetch the object from storage to a temp file (services are stateless — no
    # shared volume); parse it, then drop the temp. Keep the original extension so
    # content/format detection behaves as with a real upload.
    suffix = Path(filename or key).suffix
    fd, dest = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        objstore.fget(key, dest)
        source = source_override if source_override in load.ADAPTERS else detect_source(dest)
        if not source:
            JOBS.labels(status="skipped").inc()
            log.warning("unrecognized source", extra={"fields": {"key": key}})
            return
        with PROC.time():
            inserted = load.load(Path(dest), source)
        ROWS.inc(inserted)
        JOBS.labels(status="ok").inc()
        log.info("ingested", extra={"fields": {"key": key, "source": source, "inserted": inserted}})
    finally:
        os.unlink(dest)


def _apply_feedback(natural_key: str, category: str, corrected_by: str | None) -> int:
    """Apply a user category correction: update silver + record it in gold.

    Runs in one transaction. Returns the number of silver rows updated (0 if the
    natural_key is unknown -- the feedback is still recorded for the retrain).
    """
    engine = get_engine()
    with engine.begin() as conn:
        res = conn.execute(
            text(
                "UPDATE silver.transactions SET category = :c, "
                "category_source = 'manual', category_confidence = 1.0 "
                "WHERE natural_key = :k"
            ),
            {"c": category, "k": natural_key},
        )
        conn.execute(
            text(
                "INSERT INTO gold.category_feedback (natural_key, category, corrected_by) "
                "VALUES (:k, :c, :b)"
            ),
            {"k": natural_key, "c": category, "b": corrected_by},
        )
    return res.rowcount


async def _consume(sub, handler) -> None:
    """Pull one message from ``sub`` (if any) and run ``handler(data)``."""
    try:
        msgs = await sub.fetch(1, timeout=1)
    except Exception:
        return  # no message within the timeout
    for m in msgs:
        try:
            await handler(json.loads(m.data))
        finally:
            await m.ack()


async def _handle_ingest(data: dict) -> None:
    try:
        await asyncio.to_thread(
            _process, data.get("key"), data.get("filename"), data.get("source")
        )
    except Exception as exc:  # noqa: BLE001
        JOBS.labels(status="error").inc()
        log.error("ingest failed", extra={"fields": {"key": data.get("key"), "error": str(exc)}})


async def _handle_feedback(data: dict) -> None:
    try:
        updated = await asyncio.to_thread(
            _apply_feedback, data["natural_key"], data["category"], data.get("corrected_by")
        )
        FEEDBACK.labels(status="ok").inc()
        log.info(
            "feedback applied",
            extra={"fields": {"natural_key": data["natural_key"], "category": data["category"], "updated": updated}},
        )
    except Exception as exc:  # noqa: BLE001
        FEEDBACK.labels(status="error").inc()
        log.error("feedback failed", extra={"fields": {"error": str(exc), "data": data}})


async def main() -> None:
    port = start_metrics_server()
    nc, js = await connect_jetstream()
    ingest_sub = await js.pull_subscribe(SUBJECT_INGEST, durable="etl-worker")
    feedback_sub = await js.pull_subscribe(SUBJECT_FEEDBACK, durable="etl-feedback")
    log.info(
        "etl-worker listening",
        extra={"fields": {"subjects": [SUBJECT_INGEST, SUBJECT_FEEDBACK], "metrics_port": port}},
    )
    while True:
        await _consume(ingest_sub, _handle_ingest)
        await _consume(feedback_sub, _handle_feedback)


if __name__ == "__main__":
    asyncio.run(main())
