"""Tests for analytics service."""

from __future__ import annotations

from app.analytics.service import AnalyticsService
from app.storage.repositories import InMemoryTransactionRepository


def build_service() -> tuple[AnalyticsService, InMemoryTransactionRepository]:
    repository = InMemoryTransactionRepository()
    service = AnalyticsService(transaction_repository=repository)
    return service, repository


def seed_transaction(
    repository: InMemoryTransactionRepository,
    *,
    checkout_request_id: str,
    amount: int,
    status: str,
) -> None:
    repository.save_pending_transaction(
        phone_number="254700000000",
        amount=amount,
        account_reference="INV-001",
        description="Invoice payment",
        checkout_request_id=checkout_request_id,
        merchant_request_id=f"mock_{checkout_request_id}",
    )
    if status != "pending":
        repository.update_transaction_status(
            checkout_request_id=checkout_request_id,
            status=status,
            result_code=0 if status == "completed" else 1032,
            result_description="Callback processed.",
            mpesa_receipt_number="RCP123" if status == "completed" else None,
        )


def test_today_summary_counts_completed_failed_pending() -> None:
    service, repository = build_service()
    seed_transaction(repository, checkout_request_id="completed", amount=1_000, status="completed")
    seed_transaction(repository, checkout_request_id="failed", amount=2_000, status="failed")
    seed_transaction(repository, checkout_request_id="pending", amount=3_000, status="pending")

    summary = service.get_today_summary()

    assert summary.total_transactions == 3
    assert summary.completed_transactions == 1
    assert summary.failed_transactions == 1
    assert summary.pending_transactions == 1


def test_revenue_only_counts_completed_transactions() -> None:
    service, repository = build_service()
    seed_transaction(
        repository,
        checkout_request_id="completed-1",
        amount=1_000,
        status="completed",
    )
    seed_transaction(
        repository,
        checkout_request_id="completed-2",
        amount=2_500,
        status="completed",
    )
    seed_transaction(repository, checkout_request_id="failed", amount=9_000, status="failed")
    seed_transaction(repository, checkout_request_id="pending", amount=8_000, status="pending")

    summary = service.get_today_summary()

    assert summary.total_revenue == 3_500


def test_failed_transactions_list_returns_failed_only() -> None:
    service, repository = build_service()
    seed_transaction(repository, checkout_request_id="completed", amount=1_000, status="completed")
    seed_transaction(repository, checkout_request_id="failed-1", amount=2_000, status="failed")
    seed_transaction(repository, checkout_request_id="failed-2", amount=3_000, status="failed")
    seed_transaction(repository, checkout_request_id="pending", amount=4_000, status="pending")

    failed_transactions = service.get_failed_transactions()

    assert len(failed_transactions) == 2
    assert {transaction.checkout_request_id for transaction in failed_transactions} == {
        "failed-1",
        "failed-2",
    }
    assert all(transaction.status == "failed" for transaction in failed_transactions)


def test_empty_repository_returns_zero_summary() -> None:
    service, _repository = build_service()

    summary = service.get_today_summary()

    assert summary.total_transactions == 0
    assert summary.completed_transactions == 0
    assert summary.failed_transactions == 0
    assert summary.pending_transactions == 0
    assert summary.total_revenue == 0
