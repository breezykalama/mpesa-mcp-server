"""Transaction state machine rules."""

from __future__ import annotations

from typing import Literal

TransactionStatus = Literal["pending", "completed", "failed", "timed_out", "cancelled"]

ALLOWED_STATUSES: frozenset[str] = frozenset(
    {"pending", "completed", "failed", "timed_out", "cancelled"}
)
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "failed", "timed_out", "cancelled"}
)
ALLOWED_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("pending", "completed"),
        ("pending", "failed"),
        ("pending", "timed_out"),
        ("pending", "cancelled"),
    }
)


class TransactionStateTransitionError(ValueError):
    """Raised when a transaction status transition is not allowed."""

    def __init__(self, from_status: str, to_status: str) -> None:
        super().__init__(
            f"Invalid transaction state transition: {from_status} -> {to_status}."
        )
        self.from_status = from_status
        self.to_status = to_status


def can_transition(from_status: str, to_status: str) -> bool:
    """Return whether a transaction can move from one status to another."""

    if from_status not in ALLOWED_STATUSES or to_status not in ALLOWED_STATUSES:
        return False
    return (from_status, to_status) in ALLOWED_TRANSITIONS


def validate_transition(from_status: str, to_status: str) -> None:
    """Raise when a transaction transition is not allowed."""

    if not can_transition(from_status, to_status):
        raise TransactionStateTransitionError(from_status, to_status)

