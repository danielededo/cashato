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
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prometheus_client import Counter, Histogram  # noqa: E402
from sqlalchemy import text  # noqa: E402

from db.db import get_engine  # noqa: E402
from libs.messaging import SUBJECT_RECATEGORIZE, connect_jetstream  # noqa: E402
from libs.model_client import KServeModel  # noqa: E402
from libs.obs import setup_logging, start_metrics_server  # noqa: E402
from libs.parsers.categorize import Categorizer  # noqa: E402

log = setup_logging("categorizer")

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
                "UPDATE silver.transactions SET category = :c, "
                "category_confidence = :cf, category_source = :s WHERE id = :id"
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


async def _consume(sub) -> None:
    try:
        msgs = await sub.fetch(1, timeout=1)
    except Exception:
        return  # no message within the timeout
    for m in msgs:
        try:
            await _handle(json.loads(m.data or b"{}"))
        except Exception as exc:  # noqa: BLE001
            RUNS.labels(status="error").inc()
            log.error("recategorize failed", extra={"fields": {"error": str(exc)}})
        finally:
            await m.ack()


async def main() -> None:
    port = start_metrics_server()
    nc, js = await connect_jetstream()
    sub = await js.pull_subscribe(SUBJECT_RECATEGORIZE, durable="categorizer")
    log.info(
        "categorizer listening",
        extra={"fields": {"subject": SUBJECT_RECATEGORIZE, "metrics_port": port}},
    )
    while True:
        await _consume(sub)


if __name__ == "__main__":
    asyncio.run(main())
