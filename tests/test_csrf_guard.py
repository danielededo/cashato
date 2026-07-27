"""The ingest-api CSRF guard: browser cross-site writes are
rejected, everything legitimate passes.

Kept in the app rather than the gateway on purpose: Envoy Gateway 1.8's
SecurityPolicy header authorization is exact-match only, so "Origin present
and not loopback" is inexpressible there — and here it is unit-testable.
"""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from cashato.services.ingest_api.app import _no_cross_origin_writes


def _request(origin: str | None = None) -> Request:
    headers = [(b"origin", origin.encode())] if origin else []
    return Request({"type": "http", "method": "POST", "headers": headers})


def test_no_origin_passes():
    # curl, scripts, server-to-server: browsers are the only Origin senders.
    _no_cross_origin_writes(_request(None))


@pytest.mark.parametrize(
    "origin",
    ["http://localhost", "http://localhost:8080", "http://127.0.0.1", "http://[::1]:5173"],
)
def test_loopback_origins_pass(origin):
    _no_cross_origin_writes(_request(origin))


@pytest.mark.parametrize(
    "origin",
    ["https://evil.example", "http://localhost.evil.example", "null"],
)
def test_cross_site_origins_are_rejected(origin):
    with pytest.raises(HTTPException) as exc:
        _no_cross_origin_writes(_request(origin))
    assert exc.value.status_code == 403
