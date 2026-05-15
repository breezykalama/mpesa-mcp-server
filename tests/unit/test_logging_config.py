"""Tests for application logging configuration."""

from __future__ import annotations

import json
import logging

from app.logging.config import JsonLogFormatter, configure_logging
from app.observability.tracing import correlation_context


def test_logger_config_builds() -> None:
    configure_logging(log_level="INFO", log_format="json")

    root_logger = logging.getLogger()

    assert root_logger.level == logging.INFO
    assert isinstance(root_logger.handlers[0].formatter, JsonLogFormatter)


def test_json_formatter_emits_expected_keys() -> None:
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="Payment initiated.",
        args=(),
        exc_info=None,
    )
    record.event_type = "payment_initiated"
    record.correlation_id = "corr-123"

    payload = json.loads(formatter.format(record))

    assert payload["timestamp"].endswith("+00:00")
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["message"] == "Payment initiated."
    assert payload["event_type"] == "payment_initiated"
    assert payload["correlation_id"] == "corr-123"


def test_json_formatter_emits_context_correlation_id() -> None:
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="Payment initiated.",
        args=(),
        exc_info=None,
    )

    with correlation_context("corr-from-context"):
        payload = json.loads(formatter.format(record))

    assert payload["correlation_id"] == "corr-from-context"


def test_plain_format_works() -> None:
    configure_logging(log_level="WARNING", log_format="plain")

    root_logger = logging.getLogger()

    assert root_logger.level == logging.WARNING
    assert root_logger.handlers[0].formatter is not None
    assert not isinstance(root_logger.handlers[0].formatter, JsonLogFormatter)


def test_json_formatter_does_not_emit_secrets() -> None:
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="Daraja request started.",
        args=(),
        exc_info=None,
    )
    record.event_type = "daraja_request_started"
    record.daraja_consumer_secret = "consumer-secret"
    record.daraja_security_credential = "encrypted-credential"
    record.callback_shared_secret = "callback-secret"

    rendered = formatter.format(record)

    assert "consumer-secret" not in rendered
    assert "encrypted-credential" not in rendered
    assert "callback-secret" not in rendered
