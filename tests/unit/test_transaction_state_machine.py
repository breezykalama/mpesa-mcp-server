"""Tests for transaction state machine rules."""

from __future__ import annotations

import pytest
from app.transactions.state_machine import (
    TransactionStateTransitionError,
    can_transition,
    validate_transition,
)


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [
        ("pending", "completed"),
        ("pending", "failed"),
        ("pending", "timed_out"),
        ("pending", "cancelled"),
    ],
)
def test_allowed_pending_terminal_transitions(
    from_status: str,
    to_status: str,
) -> None:
    assert can_transition(from_status, to_status) is True
    validate_transition(from_status, to_status)


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [
        ("completed", "pending"),
        ("completed", "failed"),
        ("failed", "completed"),
        ("timed_out", "completed"),
        ("cancelled", "completed"),
        ("pending", "unknown"),
        ("unknown", "completed"),
    ],
)
def test_invalid_transitions_are_blocked(from_status: str, to_status: str) -> None:
    assert can_transition(from_status, to_status) is False
    with pytest.raises(TransactionStateTransitionError):
        validate_transition(from_status, to_status)

