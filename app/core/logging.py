"""Central logging setup: structured JSON (prod) or readable (dev), with a
request-scoped correlation id carried on every record.

Use ``get_logger(__name__)`` per module; never the root logger, never ``print``.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar

# The per-request correlation id. Middleware sets it; every log record reads it.
correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")

# Attribute names present on a vanilla LogRecord — anything else is a caller "extra".
_STANDARD_ATTRS = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime", "taskName"}


class _CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, including any structured ``extra=`` fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", correlation_id.get()),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and key != "correlation_id":
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Configure the root handler once. Idempotent within a process."""
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_CorrelationFilter())
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-7s [%(name)s] (%(correlation_id)s) %(message)s"
            )
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
