"""Correlation ID tracing utilities."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

CORRELATION_ID_HEADER = "X-Correlation-ID"

_correlation_id: ContextVar[str | None] = ContextVar(
    "correlation_id",
    default=None,
)


def generate_correlation_id() -> str:
    """Generate a UUID-based correlation ID."""

    return str(uuid4())


def get_correlation_id() -> str | None:
    """Return the current correlation ID, if one is active."""

    return _correlation_id.get()


def set_correlation_id(correlation_id: str | None = None) -> str:
    """Set and return a correlation ID for the current context."""

    resolved_correlation_id = _normalize_correlation_id(correlation_id)
    _correlation_id.set(resolved_correlation_id)
    return resolved_correlation_id


@contextmanager
def correlation_context(correlation_id: str | None = None) -> Iterator[str]:
    """Run a block with an active correlation ID."""

    existing_correlation_id = get_correlation_id()
    if existing_correlation_id is not None and correlation_id is None:
        yield existing_correlation_id
        return

    resolved_correlation_id = _normalize_correlation_id(correlation_id)
    token = _correlation_id.set(resolved_correlation_id)
    try:
        yield resolved_correlation_id
    finally:
        _correlation_id.reset(token)


def _normalize_correlation_id(correlation_id: str | None) -> str:
    if correlation_id is None or correlation_id.strip() == "":
        return generate_correlation_id()
    return correlation_id.strip()
