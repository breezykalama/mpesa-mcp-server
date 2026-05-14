"""Tests for MCP analytics tools."""

from __future__ import annotations

from datetime import UTC, datetime

from app.analytics.service import DailyAnalyticsSummary
from app.mcp.tools import get_failed_transactions_tool, get_today_summary_tool
from app.storage.repositories import PendingTransaction


class RecordingAnalyticsService:
    """Analytics service test double that records delegation."""

    def __init__(self) -> None:
        self.today_summary_called = False
        self.failed_transactions_called = False

    def get_today_summary(self) -> DailyAnalyticsSummary:
        self.today_summary_called = True
        return DailyAnalyticsSummary(
            date=datetime.now(UTC).date(),
            total_transactions=3,
            completed_transactions=1,
            failed_transactions=1,
            pending_transactions=1,
            total_revenue=1_000,
        )

    def get_failed_transactions(self) -> list[PendingTransaction]:
        self.failed_transactions_called = True
        return [
            PendingTransaction(
                transaction_id="txn_123",
                phone_number="254700000000",
                amount=1_000,
                account_reference="INV-001",
                description="Invoice payment",
                checkout_request_id="ws_CO_FAILED",
                merchant_request_id="mock_123",
                status="failed",
            )
        ]


def test_get_today_summary_tool_delegates_to_analytics_service() -> None:
    service = RecordingAnalyticsService()

    response = get_today_summary_tool({}, service)

    assert service.today_summary_called is True
    assert response.status == "ok"
    assert response.allowed is True
    assert response.data["summary"]["total_transactions"] == 3
    assert response.data["summary"]["total_revenue"] == 1_000


def test_get_failed_transactions_tool_delegates_to_analytics_service() -> None:
    service = RecordingAnalyticsService()

    response = get_failed_transactions_tool({}, service)

    assert service.failed_transactions_called is True
    assert response.status == "ok"
    assert response.allowed is True
    assert len(response.data["transactions"]) == 1
    assert response.data["transactions"][0]["status"] == "failed"
