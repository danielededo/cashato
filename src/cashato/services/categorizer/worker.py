"""categorizer — event-driven model categorization (C6d).

Consumes ``category.recategorize`` from NATS (emitted by the etl-worker after an
ingest). Re-runs the resolver chain (MCC -> MODEL -> rules -> default) over the
silver rows the fast-path left to ``rule``/``default``, using the KServe predictor
as the model (``KServeModel``, HTTP). Rows resolved by ``mcc`` (exact) or ``manual``
(user correction) are never touched.

Light service (svc image, no torch): the heavy embedding model lives in the KServe
predictor pod. Writes silver as the etl_writer role. Metrics on :9100.
"""

from __future__ import annotations

import asyncio

from opentelemetry import trace
from prometheus_client import Counter, Histogram
from sqlalchemy import text

from cashato.db.db import get_engine
from cashato.messaging import (
    SUBJECT_RECATEGORIZE,
    connect_jetstream,
    consume_one,
    ensure_consumer,
)
from cashato.model_client import KServeModel
from cashato.obs import (
    setup_logging,
    setup_tracing,
    start_metrics_server,
    tracing_enabled,
)
from cashato.parsers.categorize import Categorizer

log = setup_logging("categorizer")
setup_tracing("categorizer")
if tracing_enabled():
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
    from opentelemetry.instrumentation.urllib import URLLibInstrumentor

    PsycopgInstrumentor().instrument()
    URLLibInstrumentor().instrument()  # traces the KServe predictor HTTP call
# Safe no-op tracer when tracing is disabled (no provider installed).
tracer = trace.get_tracer("categorizer")

RUNS = Counter("cashato_categorizer_runs_total", "Recategorize runs", ["status"])
ROWS = Counter("cashato_categorizer_rows_total", "Rows recategorized")
DUR = Histogram("cashato_categorizer_seconds", "Recategorize duration (s)")


def _recategorize() -> int:
    """Recategorize the fast-path-unresolved rows via the model. Returns row count."""
    cat = Categorizer.load(model=KServeModel())
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT id, description, source, mcc FROM silver.transactions "
                "WHERE category_source IN ('rule','default')"
            )
        ).all()
        if not rows:
            return 0
        results = cat.resolve_many([(r.description, r.source, r.mcc) for r in rows])
        conn.execute(
            text(
                # Repeat the provenance filter from the SELECT: a POST /feedback
                # can flip a row to 'manual' while the model inference runs, and
                # an id-only UPDATE would clobber that correction on the way back.
                "UPDATE silver.transactions SET category = :c, "
                "category_confidence = :cf, category_source = :s "
                "WHERE id = :id AND category_source IN ('rule', 'default')"
            ),
            [
                {"c": res.code, "cf": res.confidence, "s": res.source, "id": r.id}
                for r, res in zip(rows, results, strict=True)
            ],
        )
    return len(rows)


async def _handle(_data: dict) -> None:
    with DUR.time():
        n = await asyncio.to_thread(_recategorize)
    ROWS.inc(n)
    RUNS.labels(status="ok").inc()
    log.info("recategorized", extra={"fields": {"rows": n}})


async def _handle_counted(data: dict) -> None:
    """Wrap the handler so a failure is counted and then RE-RAISED.

    Swallowing it and acking anyway dropped the recategorize request outright —
    on a WorkQueue stream the ack deletes the message. Raising lets the shared
    consumer nak, so a KServe hiccup is retried rather than leaving the rows
    uncategorized until the next ingest happens to nudge it.
    """
    try:
        await _handle(data)
    except Exception as exc:
        RUNS.labels(status="error").inc()
        log.error("recategorize failed", extra={"fields": {"error": str(exc)}})
        raise


async def main() -> None:
    port = start_metrics_server()
    nc, js = await connect_jetstream()
    sub = await ensure_consumer(js, SUBJECT_RECATEGORIZE, "categorizer", log=log)
    log.info(
        "categorizer listening",
        extra={"fields": {"subject": SUBJECT_RECATEGORIZE, "metrics_port": port}},
    )
    while True:
        await consume_one(
            sub, _handle_counted, log=log, tracer=tracer, span_name="categorizer.recategorize"
        )


if __name__ == "__main__":
    asyncio.run(main())
