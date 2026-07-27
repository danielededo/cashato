"""Observability helpers: structured JSON logging + Prometheus metrics.

Logs go to stdout as JSON (scraped by a log agent in k8s); metrics are exposed
on an HTTP endpoint.
"""

from __future__ import annotations

import json
import logging
import os
import sys


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # structured extra fields: logger.info("msg", extra={"fields": {...}})
        for k, v in getattr(record, "fields", {}).items():
            payload[k] = v
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(service: str) -> logging.Logger:
    """Configure JSON logging to stdout and return the service logger."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
    return logging.getLogger(service)


def start_metrics_server(default_port: int = 9100) -> int:
    """Expose Prometheus metrics over HTTP (for non-HTTP services like the worker)."""
    from prometheus_client import start_http_server

    port = int(os.environ.get("METRICS_PORT", default_port))
    start_http_server(port)
    return port


# --- Distributed tracing (OpenTelemetry -> OTLP -> Alloy -> Tempo) -----------
# All OTel imports are kept INSIDE these functions so images without the otel
# packages (train/predictor) can still import this module for logging/metrics.

def tracing_enabled() -> bool:
    """Tracing is on iff an OTLP endpoint is configured (set in k8s, unset locally)."""
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))


def setup_tracing(service: str) -> None:
    """Wire an OTLP span exporter to Alloy (batched). No-op when no endpoint is set.

    The endpoint comes from the standard ``OTEL_EXPORTER_OTLP_ENDPOINT`` env
    (e.g. ``http://alloy.observability.svc:4317``); local dev leaves it unset, so
    the global tracer stays the no-op default and spans cost nothing. Callers wire
    the actual instrumentation (FastAPI/psycopg/urllib) themselves, gated on
    ``tracing_enabled()``.
    """
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    # Idempotent: only install a real provider once (repeated calls are no-ops).
    if isinstance(trace.get_tracer_provider(), TracerProvider):
        return
    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
    )
    trace.set_tracer_provider(provider)


def inject_trace_headers() -> dict[str, str] | None:
    """Serialize the current trace context into W3C headers for a NATS message.

    Returns ``None`` when tracing is off or there is no active span, so the caller
    can pass ``headers=inject_trace_headers()`` unconditionally.
    """
    if not tracing_enabled():
        return None
    from opentelemetry.propagate import inject

    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier or None


def extract_trace_context(headers):
    """Rebuild a parent context from a NATS message's W3C headers (empty if none)."""
    from opentelemetry.propagate import extract

    return extract(headers or {})
