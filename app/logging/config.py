"""Application logging configuration."""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

LOG_RECORD_RESERVED_KEYS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}

SAFE_EXTRA_KEYS = {
    "amount",
    "approval_id",
    "correlation_id",
    "event_type",
    "limit",
    "remaining",
    "reset_after_seconds",
    "status",
    "tool_name",
    "transaction_id",
}


class JsonLogFormatter(logging.Formatter):
    """Format log records as compact JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as JSON."""

        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key in SAFE_EXTRA_KEYS:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value

        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(*, log_level: str = "INFO", log_format: str = "json") -> None:
    """Configure application logging."""

    level = _resolve_log_level(log_level)
    handler = logging.StreamHandler()
    if log_format == "plain":
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        )
        formatter.converter = time.gmtime
        handler.setFormatter(
            formatter
        )
    else:
        handler.setFormatter(JsonLogFormatter())

    logging.basicConfig(level=level, handlers=[handler], force=True)


def get_safe_extra(record: logging.LogRecord) -> dict[str, Any]:
    """Return safe custom extras from a log record."""

    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in LOG_RECORD_RESERVED_KEYS and key in SAFE_EXTRA_KEYS
    }


def _resolve_log_level(log_level: str) -> int:
    level = logging.getLevelName(log_level.upper())
    if isinstance(level, int):
        return level
    return logging.INFO
