"""How a NATS message gets 'settled'.

On a WorkQueue stream an ack DELETES the message, so acking a failed job
loses the ingest forever. These tests pin the four possible outcomes, which
used to be a single one: ack in a `finally`, always.
"""

import asyncio
import json
import logging
import types

import pytest

from cashato import messaging


class _Meta:
    def __init__(self, num_delivered):
        self.num_delivered = num_delivered


class _Msg:
    """Fake message that records how it was settled."""

    def __init__(self, payload=b"{}", num_delivered=1):
        self.data = payload
        self.headers = None
        self.subject = "test.subject"
        self.metadata = _Meta(num_delivered)
        self.settled = None
        self.nak_delay = None

    async def ack(self):
        self.settled = "ack"

    async def term(self):
        self.settled = "term"

    async def nak(self, delay=None):
        self.settled = "nak"
        self.nak_delay = delay


class _Sub:
    def __init__(self, msgs=None, raises=None):
        self._msgs = msgs or []
        self._raises = raises

    async def fetch(self, _n, timeout=None):
        if self._raises:
            raise self._raises
        if not self._msgs:
            raise TimeoutError
        return self._msgs


class _NoopSpan:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_TRACER = types.SimpleNamespace(start_as_current_span=lambda *a, **k: _NoopSpan())


def _run(sub, handler, log=None):
    return asyncio.run(
        messaging.consume_one(
            sub,
            handler,
            log=log or logging.getLogger("test"),
            tracer=_TRACER,
            span_name="test",
        )
    )


async def _ok(_data):
    return None


async def _boom(_data):
    raise RuntimeError("postgres unavailable")


class TestSettlement:
    def test_success_acks(self):
        m = _Msg()
        assert _run(_Sub([m]), _ok) is True
        assert m.settled == "ack"

    def test_transient_failure_naks_for_redelivery(self):
        # the case that used to lose the ingest: a one-second blip on MinIO or
        # Postgres got acked and the message deleted
        m = _Msg(num_delivered=1)
        _run(_Sub([m]), _boom)
        assert m.settled == "nak"
        assert m.nak_delay == messaging.NAK_DELAY_SECONDS

    def test_gives_up_once_the_delivery_budget_is_spent(self):
        # a poison job must not be redelivered forever
        m = _Msg(num_delivered=messaging.MAX_DELIVER)
        _run(_Sub([m]), _boom)
        assert m.settled == "term"

    def test_malformed_payload_is_terminated_not_retried(self):
        # it will never parse: redelivering it is a guaranteed loop
        m = _Msg(payload=b"{not json")
        _run(_Sub([m]), _ok)
        assert m.settled == "term"

    def test_handler_receives_the_decoded_payload(self):
        seen = {}

        async def handler(data):
            seen.update(data)

        _run(_Sub([_Msg(payload=json.dumps({"key": "abc"}).encode())]), handler)
        assert seen == {"key": "abc"}


class TestFetchErrors:
    def test_timeout_is_the_idle_path_and_is_silent(self, caplog):
        with caplog.at_level(logging.WARNING):
            assert _run(_Sub([]), _ok) is False
        assert caplog.records == []

    def test_real_fetch_error_is_reported_not_mistaken_for_idle(self, caplog, monkeypatch):
        # a conflicting consumer config or a deleted stream used to be mistaken
        # for "no message": the worker looked healthy while every upload sat
        # in the queue
        monkeypatch.setattr(messaging.asyncio, "sleep", _ok)
        sub = _Sub(raises=RuntimeError("consumer config conflict"))
        with caplog.at_level(logging.ERROR):
            assert _run(sub, _ok) is False
        assert any("nats fetch failed" in r.message for r in caplog.records)


@pytest.mark.parametrize("durable", ["etl-worker", "categorizer"])
def test_consumer_config_bounds_redelivery(durable):
    cfg = messaging.consumer_config(durable, "test.subject")
    assert cfg.durable_name == durable
    assert cfg.filter_subject == "test.subject"
    assert cfg.max_deliver == messaging.MAX_DELIVER
    # ack_wait must exceed the slowest job (a big PDF), or JetStream redelivers
    # while the first attempt is still running
    assert cfg.ack_wait >= 60
