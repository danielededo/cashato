"""etl-worker — consumes ingestion jobs from NATS and populates the DB.

For each job: determine the source (explicit override or ``detect_source``),
parse with the right adapter, normalize, dedup and persist (bronze + silver)
with the **fast-path** category (MCC + rules). ML categorization is a separate
concern; the etl-worker stays lightweight (no torch/model).

Observability: structured JSON logs to stdout + Prometheus metrics on
``METRICS_PORT`` (default 9100).
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
from cashato.cli import link_transfers, load  # reusable loader + transfer relink
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
        # ingest-api 422s unknown overrides; this guard fires loudly rather
        # than falling back silently.
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
            inserted = load.load(Path(dest), source, force=force, filename=filename)
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

    Deliberately RE-RAISES: on a WorkQueue stream an ack deletes the message,
    so letting the exception out lets the consumer nak and JetStream redeliver.
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
        if inserted:
            # The gold spend views exclude transfer-tagged legs, so the tagging
            # must follow EVERY batch of new rows.
            try:
                pairs, moved, _net = await asyncio.to_thread(link_transfers.relink_all)
            except Exception as exc:  # noqa: BLE001
                # Rows ARE loaded; a failed relink degrades the views but must
                # not fail (and re-run) the whole ingest.
                log.error("transfer relink failed", extra={"fields": {"error": str(exc)}})
            else:
                log.info(
                    "transfers relinked",
                    extra={"fields": {"pairs": pairs, "volume": str(moved)}},
                )
        # Ask the categorizer to run the model. Also on force jobs with zero
        # inserts: a reprocess after a model retrain dedups everything
        # (inserted == 0), and the nudge was the only thing that would apply
        # the new model to existing rows.
        if inserted or data.get("force"):
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

    async def mark_ingest_given_up(data: dict, exc: Exception) -> None:
        """Leave a visible trace when an ingest job exhausts its retry budget.

        The message is gone from the WorkQueue, so nothing else will ever
        update it.
        """
        filename = data.get("filename") or data.get("key")

        def _mark() -> int:
            with get_engine().begin() as conn:
                return conn.execute(
                    text(
                        "UPDATE bronze.raw_files SET status = 'failed', "
                        "error = :e WHERE filename = :f AND status = 'pending'"
                    ),
                    {"e": f"gave up after retries: {str(exc)[:500]}", "f": filename},
                ).rowcount

        updated = await asyncio.to_thread(_mark)
        if not updated:
            log.error(
                "gave up on a job with no pending raw_files row — the upload "
                "left no trace; a /admin/reprocess will re-enqueue it",
                extra={"fields": {"filename": filename}},
            )

    while True:
        await consume_one(
            ingest_sub,
            handle_ingest_and_notify,
            log=log,
            tracer=tracer,
            span_name="etl.ingest",
            on_giving_up=mark_ingest_given_up,
        )
        await consume_one(
            feedback_sub, _handle_feedback, log=log, tracer=tracer, span_name="etl.feedback"
        )


if __name__ == "__main__":
    asyncio.run(main())
