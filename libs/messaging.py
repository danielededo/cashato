"""Shared NATS JetStream helper used by the services (ingest-api, etl-worker).

Two subjects on one stream:
- ``ingest.jobs`` — a file was uploaded and must be parsed/loaded;
- ``category.feedback`` — a user corrected a transaction's category (active
  learning); the consumer applies it to silver + records it in gold.

The feedback consumer lives in the etl-worker for now; in phase C it moves to the
dedicated ``categorizer`` service (same event, same handler).
"""

from __future__ import annotations

import contextlib
import os

import nats
from nats.js.api import RetentionPolicy, StreamConfig

NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")

SUBJECT_INGEST = "ingest.jobs"
SUBJECT_FEEDBACK = "category.feedback"
SUBJECTS = [SUBJECT_INGEST, SUBJECT_FEEDBACK]
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
    # stream may already exist (same config) -> ignore
    with contextlib.suppress(Exception):
        await js.add_stream(config=config)
    return nc, js
