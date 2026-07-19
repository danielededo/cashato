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

NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")

SUBJECT_INGEST = "ingest.jobs"
SUBJECT_FEEDBACK = "category.feedback"
SUBJECTS = [SUBJECT_INGEST, SUBJECT_FEEDBACK]
STREAM = "CASHATO"

# Backwards-compatible alias (ingest job subject was previously ``SUBJECT``).
SUBJECT = SUBJECT_INGEST


async def connect_jetstream():
    """Connect to NATS and ensure the JetStream stream exists."""
    nc = await nats.connect(NATS_URL)
    js = nc.jetstream()
    # stream may already exist (same config) -> ignore
    with contextlib.suppress(Exception):
        await js.add_stream(name=STREAM, subjects=SUBJECTS)
    return nc, js
