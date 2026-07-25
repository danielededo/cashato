"""Come viene 'chiuso' un messaggio NATS.

Su uno stream WorkQueue l'ack CANCELLA il messaggio, quindi ackare un job
fallito perde l'ingest per sempre. Questi test fissano le quattro uscite
possibili, che prima erano una sola: ack in un `finally`, sempre.
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
    """Messaggio finto che registra come è stato chiuso."""

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
        # il caso che prima perdeva l'ingest: un blip di un secondo su MinIO o
        # Postgres veniva ackato e il messaggio cancellato
        m = _Msg(num_delivered=1)
        _run(_Sub([m]), _boom)
        assert m.settled == "nak"
        assert m.nak_delay == messaging.NAK_DELAY_SECONDS

    def test_gives_up_once_the_delivery_budget_is_spent(self):
        # un poison job non deve essere riconsegnato all'infinito
        m = _Msg(num_delivered=messaging.MAX_DELIVER)
        _run(_Sub([m]), _boom)
        assert m.settled == "term"

    def test_malformed_payload_is_terminated_not_retried(self):
        # non si parserà mai: riconsegnarlo è un ciclo garantito
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
        # una consumer-config in conflitto o uno stream cancellato venivano
        # scambiati per "nessun messaggio": il worker sembrava sano mentre ogni
        # upload restava in coda
        monkeypatch.setattr(messaging.asyncio, "sleep", _ok)
        sub = _Sub(raises=RuntimeError("consumer config conflict"))
        with caplog.at_level(logging.ERROR):
            assert _run(sub, _ok) is False
        assert any("nats fetch failed" in r.message for r in caplog.records)


@pytest.mark.parametrize("durable", ["etl-worker", "categorizer"])
def test_consumer_config_bounds_redelivery(durable):
    cfg = messaging.consumer_config(durable)
    assert cfg.durable_name == durable
    assert cfg.max_deliver == messaging.MAX_DELIVER
    # ack_wait deve superare il job più lento (un PDF grosso), altrimenti
    # JetStream riconsegna mentre il primo tentativo sta ancora girando
    assert cfg.ack_wait >= 60
