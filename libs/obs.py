"""Observability helpers: structured JSON logging + Prometheus metrics.

Code-level instrumentation (usable now); the collection/visualization stack
(Prometheus, Loki, Tempo, Grafana) is deployed in phase C. Logs go to stdout as
JSON (scraped by a log agent in k8s); metrics are exposed on an HTTP endpoint.
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
