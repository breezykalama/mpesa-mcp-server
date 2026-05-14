"""Analytics service."""

from __future__ import annotations

from datetime import UTC, date, datetime

from pydantic import BaseModel

from app.storage.repositories import PendingTransaction, TransactionRepositoryProtocol


class DailyAnalyticsSummary(BaseModel):
    """Daily M-Pesa transaction analytics summary."""

    date: date
    total_transactions: int
    completed_transactions: int
    failed_transactions: int
    pending_transactions: int
    total_revenue: int


class AnalyticsService:
    """Read-only analytics service for M-Pesa transactions."""

    def __init__(self, *, transaction_repository: TransactionRepositoryProtocol) -> None:
        self._transaction_repository = transaction_repository

    def get_today_summary(self) -> DailyAnalyticsSummary:
        """Return today's transaction summary."""

        today = datetime.now(UTC).date()
        transactions = self._transaction_repository.list_transactions_for_date(today)
        completed_transactions = [
            transaction for transaction in transactions if transaction.status == "completed"
        ]
        failed_transactions = [
            transaction for transaction in transactions if transaction.status == "failed"
        ]
        pending_transactions = [
            transaction for transaction in transactions if transaction.status == "pending"
        ]

        return DailyAnalyticsSummary(
            date=today,
            total_transactions=len(transactions),
            completed_transactions=len(completed_transactions),
            failed_transactions=len(failed_transactions),
            pending_transactions=len(pending_transactions),
            total_revenue=sum(transaction.amount for transaction in completed_transactions),
        )

    def get_failed_transactions(self) -> list[PendingTransaction]:
        """Return failed transactions."""

        return self._transaction_repository.list_transactions_by_status("failed")
