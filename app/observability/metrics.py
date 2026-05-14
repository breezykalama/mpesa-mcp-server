"""In-memory metrics recording."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel


class MetricsSnapshot(BaseModel):
    """Operational metrics snapshot."""

    tool_call_count: int = 0
    successful_payment_count: int = 0
    failed_payment_count: int = 0
    approval_required_count: int = 0
    callback_received_count: int = 0
    receipt_generated_count: int = 0


class MetricsRecorder(Protocol):
    """Interface for recording operational metrics."""

    def increment(self, counter_name: str, amount: int = 1) -> None:
        """Increment a named counter."""

    def snapshot(self) -> MetricsSnapshot:
        """Return a metrics snapshot."""


class InMemoryMetricsRecorder:
    """Simple in-memory metrics recorder."""

    def __init__(self) -> None:
        self._snapshot = MetricsSnapshot()

    def increment(self, counter_name: str, amount: int = 1) -> None:
        """Increment a named counter."""

        current_value = getattr(self._snapshot, counter_name)
        setattr(self._snapshot, counter_name, current_value + amount)

    def snapshot(self) -> MetricsSnapshot:
        """Return a metrics snapshot."""

        return self._snapshot.model_copy()
