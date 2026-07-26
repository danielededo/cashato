"""Shared NATS JetStream helper used by the services (ingest-api, etl-worker).

Two subjects on one stream:
- ``ingest.jobs`` — a file was uploaded and must be parsed/loaded;
- ``category.feedback`` — a user corrected a transaction's category (active
  learning); the consumer applies it to silver + records it in gold.

The feedback consumer lives in the etl-worker for now; in phase C it moves to the
dedicated ``categorizer`` service (same event, same handler).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os

import nats
from nats.js.api import AckPolicy, ConsumerConfig, RetentionPolicy, StreamConfig

from cashato.obs import extract_trace_context

NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")

SUBJECT_INGEST = "ingest.jobs"
SUBJECT_FEEDBACK = "category.feedback"
# Emitted by the etl-worker after an ingest; consumed by the categorizer service
# (C6d), which runs the model (via KServe) over the newly landed rows.
SUBJECT_RECATEGORIZE = "category.recategorize"
SUBJECTS = [SUBJECT_INGEST, SUBJECT_FEEDBACK, SUBJECT_RECATEGORIZE]
STREAM = "CASHATO"

# Backwards-compatible alias (ingest job subject was previously ``SUBJECT``).
SUBJECT = SUBJECT_INGEST

# Keep the JetStream fileStore PVC bounded (C4). Both subjects carry work-queue
# semantics — a job/feedback event is consumed once and acked, then it can go —
# so WorkQueue retention deletes each message on ack. MaxAge is a safety cap so
# an un-acked message (e.g. a poison job that always fails) can't pin the PVC
# forever. Payloads are tiny file *references*, so this stays negligible anyway.
# max_age is in SECONDS (nats-py converts to nanoseconds internally).
STREAM_MAX_AGE_SECONDS = float(
    os.environ.get("NATS_STREAM_MAX_AGE_SECONDS", 7 * 24 * 3600)
)


# Delivery policy for the pull consumers. WorkQueue retention DELETES a message
# on ack, so an ack is irreversible: acking a job that failed loses the ingest
# outright, and only a manual /admin/reprocess would recover it. Hence explicit
# redelivery instead — nak on failure, bounded by max_deliver, then term.
ACK_WAIT_SECONDS = float(os.environ.get("NATS_ACK_WAIT_SECONDS", 300))
MAX_DELIVER = int(os.environ.get("NATS_MAX_DELIVER", 5))
NAK_DELAY_SECONDS = float(os.environ.get("NATS_NAK_DELAY_SECONDS", 30))


def consumer_config(durable: str, subject: str) -> ConsumerConfig:
    """Pull-consumer config with a redelivery budget.

    ``ack_wait`` must exceed the slowest realistic job (parsing a large PDF), or
    JetStream redelivers while the first attempt is still running and the same
    file is processed twice. ``max_deliver`` bounds a poison message server-side.
    """
    return ConsumerConfig(
        durable_name=durable,
        filter_subject=subject,
        ack_policy=AckPolicy.EXPLICIT,
        ack_wait=ACK_WAIT_SECONDS,
        max_deliver=MAX_DELIVER,
    )


async def ensure_consumer(js, subject: str, durable: str, *, log):
    """Bind a pull subscription with the delivery budget ACTUALLY applied.

    ``pull_subscribe(config=...)`` silently ignores the config when the durable
    already exists — it just binds to whatever is on the server. That is how a
    deployed worker ended up running with the defaults (``ack_wait`` 30s,
    ``max_deliver`` unlimited) while the code asked for 300s and 5, with nothing
    reporting the mismatch. So reconcile explicitly with ``add_consumer`` (which
    is create-or-update), then READ BACK what the server accepted and warn if it
    still differs, rather than assuming it took.
    """
    cfg = consumer_config(durable, subject)
    try:
        await js.add_consumer(STREAM, config=cfg)
    except Exception as exc:  # noqa: BLE001 - reconcile is best-effort, binding is not
        log.warning(
            "could not reconcile consumer config",
            extra={"fields": {"durable": durable, "error": str(exc)}},
        )

    sub = await js.pull_subscribe(subject, durable=durable)

    # Deliberately NOT wrapped in suppress: this IS the check that the config
    # took, and swallowing its failure would put us back to assuming.
    try:
        live = (await js.consumer_info(STREAM, durable)).config
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "could not read back consumer config",
            extra={"fields": {"durable": durable, "error": str(exc)}},
        )
        return sub

    if live.ack_wait != ACK_WAIT_SECONDS or live.max_deliver != MAX_DELIVER:
        log.warning(
            "consumer config not applied; client-side retry budget still holds",
            extra={
                "fields": {
                    "durable": durable,
                    "server_ack_wait": live.ack_wait,
                    "server_max_deliver": live.max_deliver,
                    "wanted_ack_wait": ACK_WAIT_SECONDS,
                    "wanted_max_deliver": MAX_DELIVER,
                }
            },
        )
    else:
        log.info(
            "consumer ready",
            extra={
                "fields": {
                    "durable": durable,
                    "ack_wait": live.ack_wait,
                    "max_deliver": live.max_deliver,
                }
            },
        )
    return sub


async def consume_one(sub, handler, *, log, tracer, span_name: str, on_giving_up=None) -> bool:
    """Pull at most one message and settle it explicitly. Returns True if one ran.

    Settlement rules, all deliberate:
    - **malformed payload → term.** It will never parse; redelivering it is a
      guaranteed loop.
    - **handler raised → nak** (delayed), so a transient blip — MinIO or Postgres
      unavailable for a second — is retried instead of silently dropped. Once the
      delivery budget is spent the message is termed and logged as given up, so
      it neither loops forever nor disappears without a trace.
    - **success → ack**, which on WorkQueue removes it.

    A fetch timeout means "no message" and is the normal idle path. Anything else
    — a deleted stream, a durable-config conflict after a stream recreate, a
    permissions error — is a real outage and is logged and counted rather than
    mistaken for idleness, which would leave the worker looking healthy while
    every upload silently queued.
    """
    try:
        msgs = await sub.fetch(1, timeout=1)
    except TimeoutError:
        return False
    except Exception as exc:  # noqa: BLE001 - broken consumer, not an idle one
        log.error("nats fetch failed", extra={"fields": {"error": str(exc)}})
        # Back off so a persistently broken consumer does not spin a hot loop.
        await asyncio.sleep(NAK_DELAY_SECONDS)
        return False

    for m in msgs:
        ctx = extract_trace_context(m.headers)
        with tracer.start_as_current_span(span_name, context=ctx):
            try:
                data = json.loads(m.data)
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "malformed message, dropping",
                    extra={"fields": {"error": str(exc), "subject": m.subject}},
                )
                await m.term()
                continue
            try:
                await handler(data)
            except Exception as exc:  # noqa: BLE001
                delivered = getattr(m.metadata, "num_delivered", 1)
                if delivered >= MAX_DELIVER:
                    log.error(
                        "job failed permanently, giving up",
                        extra={"fields": {"error": str(exc), "delivered": delivered}},
                    )
                    if on_giving_up is not None:
                        # Best-effort: let the owner leave a visible trace (e.g.
                        # mark the file failed) — a termed job used to leave the
                        # upload as 'pending' forever, indistinguishable from a
                        # queued one.
                        try:
                            await on_giving_up(data, exc)
                        except Exception as hook_exc:  # noqa: BLE001
                            log.error(
                                "giving-up hook failed",
                                extra={"fields": {"error": str(hook_exc)}},
                            )
                    await m.term()
                else:
                    log.warning(
                        "job failed, will retry",
                        extra={"fields": {"error": str(exc), "delivered": delivered}},
                    )
                    await m.nak(delay=NAK_DELAY_SECONDS)
                continue
            await m.ack()
    return bool(msgs)


async def connect_jetstream():
    """Connect to NATS and ensure the JetStream stream exists (WorkQueue + MaxAge).

    Note: JetStream forbids changing retention on an existing stream. A stream
    left over from an older LimitsPolicy deploy must be deleted before this
    config applies — the add_stream error is suppressed, so callers should
    recreate the stream when migrating an existing environment.
    """
    nc = await nats.connect(NATS_URL)
    js = nc.jetstream()
    config = StreamConfig(
        name=STREAM,
        subjects=SUBJECTS,
        retention=RetentionPolicy.WORK_QUEUE,
        max_age=STREAM_MAX_AGE_SECONDS,
    )
    # Create the stream, or reconcile it if it already exists. update_stream picks
    # up a NEWLY ADDED subject (allowed) on an existing stream; retention is
    # unchanged here so the update stays legal (JetStream forbids retention changes).
    try:
        await js.add_stream(config=config)
    except Exception:
        with contextlib.suppress(Exception):
            await js.update_stream(config=config)
    return nc, js
