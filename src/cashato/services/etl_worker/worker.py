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
import os
import tempfile
from pathlib import Path

from opentelemetry import trace
from prometheus_client import Counter, Histogram
from sqlalchemy import text

from cashato import objstore
from cashato.cli import load  # (reusable loader: parse -> bronze/silver + fast-path)
from cashato.db.db import get_engine
from cashato.messaging import (
    SUBJECT_FEEDBACK,
    SUBJECT_INGEST,
    SUBJECT_RECATEGORIZE,
    connect_jetstream,
    consume_one,
    ensure_consumer,
)
from cashato.obs import (
    inject_trace_headers,
    setup_logging,
    setup_tracing,
    start_metrics_server,
    tracing_enabled,
)
from cashato.parsers.detect import detect_candidates, detect_source, identify_bank

log = setup_logging("etl-worker")
setup_tracing("etl-worker")
if tracing_enabled():
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

    PsycopgInstrumentor().instrument()
# Safe no-op tracer when tracing is disabled (no provider installed).
tracer = trace.get_tracer("etl-worker")

JOBS = Counter("cashato_etl_jobs_total", "ETL jobs processed", ["status"])
ROWS = Counter("cashato_etl_rows_ingested_total", "New rows inserted into silver")
PROC = Histogram("cashato_etl_process_seconds", "Job processing time (s)")
FEEDBACK = Counter("cashato_etl_feedback_total", "Category corrections applied", ["status"])


def _process(key: str, filename: str | None, source_override: str | None, force: bool = False) -> int:
    # Fetch the object from storage to a temp file (services are stateless — no
    # shared volume); parse it, then drop the temp. Keep the original extension so
    # content/format detection behaves as with a real upload.
    suffix = Path(filename or key).suffix
    fd, dest = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        objstore.fget(key, dest)
        # ingest-api 422s unknown overrides, so this guard only fires for jobs
        # queued before that gate existed — loudly, not as a silent fallback.
        if source_override and source_override not in load.ADAPTERS:
            log.warning("unknown source override %r ignored; detecting", source_override)
            source_override = None
        source = source_override or detect_source(dest)
        if not source:
            # Two different declines: nothing matched, or several sources matched
            # equally and guessing would be a coin flip. Record which, because the
            # user's next step differs — and either way the IBAN usually still
            # names the bank, which beats dropping the file with no trace.
            tied = [s for s, _ in detect_candidates(dest)]
            bank = identify_bank(dest)
            load.record_unsupported(
                Path(dest), filename or key, bank, ambiguous=tied if len(tied) > 1 else None
            )
            JOBS.labels(status="skipped").inc()
            log.warning(
                "source not resolved",
                extra={"fields": {"key": key, "bank": bank, "candidates": tied}},
            )
            return 0
        with PROC.time():
            # force: an ordinary upload stops at the sha256 check (cheap dedup of
            # a re-upload), but an admin reprocess exists precisely to re-parse
            # files already marked 'parsed'. Safe either way — silver dedups on
            # natural_key, so a re-parse inserts nothing it already has.
            inserted = load.load(Path(dest), source, force=force)
        ROWS.inc(inserted)
        JOBS.labels(status="ok").inc()
        log.info("ingested", extra={"fields": {"key": key, "source": source, "inserted": inserted}})
        return inserted
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


async def _handle_ingest(data: dict) -> int:
    """Process one ingest job. Returns the number of rows inserted.

    Deliberately RE-RAISES. It used to swallow every exception and return 0,
    while the consumer acked in a `finally` — and on a WorkQueue stream an ack
    deletes the message, so a one-second Postgres or MinIO blip destroyed the
    ingest with no way back except a manual /admin/reprocess. Letting it out
    lets the consumer nak and have JetStream redeliver.
    """
    try:
        return await asyncio.to_thread(
            _process,
            data["key"],
            data.get("filename"),
            data.get("source"),
            bool(data.get("force")),
        )
    except Exception as exc:
        JOBS.labels(status="error").inc()
        log.error("ingest failed", extra={"fields": {"key": data.get("key"), "error": str(exc)}})
        raise


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
    except Exception as exc:
        FEEDBACK.labels(status="error").inc()
        log.error("feedback failed", extra={"fields": {"error": str(exc), "data": data}})
        raise  # let the consumer retry rather than drop the correction


async def main() -> None:
    port = start_metrics_server()
    nc, js = await connect_jetstream()
    ingest_sub = await ensure_consumer(js, SUBJECT_INGEST, "etl-worker", log=log)
    feedback_sub = await ensure_consumer(js, SUBJECT_FEEDBACK, "etl-feedback", log=log)
    log.info(
        "etl-worker listening",
        extra={"fields": {"subjects": [SUBJECT_INGEST, SUBJECT_FEEDBACK], "metrics_port": port}},
    )

    async def handle_ingest_and_notify(data: dict) -> None:
        inserted = await _handle_ingest(data)
        # New rows landed -> ask the categorizer to run the model over them.
        if inserted:
            try:
                # Propagate the current trace context so the categorizer's run
                # links back to this ingest (one end-to-end trace).
                await js.publish(
                    SUBJECT_RECATEGORIZE, b"{}", headers=inject_trace_headers()
                )
            except Exception as exc:  # noqa: BLE001
                # Best-effort: the rows ARE loaded, so do not fail the job over a
                # missed nudge — but say so, instead of vanishing.
                log.warning("recategorize request failed", extra={"fields": {"error": str(exc)}})
            else:
                log.info("recategorize requested", extra={"fields": {"inserted": inserted}})

    while True:
        await consume_one(
            ingest_sub, handle_ingest_and_notify, log=log, tracer=tracer, span_name="etl.ingest"
        )
        await consume_one(
            feedback_sub, _handle_feedback, log=log, tracer=tracer, span_name="etl.feedback"
        )


if __name__ == "__main__":
    asyncio.run(main())
