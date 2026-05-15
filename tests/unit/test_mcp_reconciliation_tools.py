"""Tests for reconciliation MCP tool wrapper."""

from __future__ import annotations

from app.mcp.tools import run_reconciliation_tool
from app.reconciliation.service import ReconciliationSummary


class RecordingReconciliationService:
    """Reconciliation service test double."""

    def __init__(self) -> None:
        self.called = False

    def run_reconciliation(self) -> ReconciliationSummary:
        self.called = True
        return ReconciliationSummary(checked_transactions=1)


def test_run_reconciliation_tool_delegates_to_service() -> None:
    service = RecordingReconciliationService()

    response = run_reconciliation_tool({}, service)

    assert response.status == "ok"
    assert response.allowed is True
    assert service.called is True
    assert response.data["summary"]["checked_transactions"] == 1


def test_run_reconciliation_tool_rejects_invalid_input() -> None:
    service = RecordingReconciliationService()

    response = run_reconciliation_tool({"unexpected": "value"}, service)

    assert response.status == "invalid_input"
    assert response.allowed is False
    assert service.called is False
