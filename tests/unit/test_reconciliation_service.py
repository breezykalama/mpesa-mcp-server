"""Tests for read-only reconciliation service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.payments.providers import PaymentInitiationResponse, PaymentStatusResponse
from app.reconciliation.service import ReconciliationService
from app.storage.repositories import InMemoryTransactionRepository, PendingTransaction


def test_reconciliation_detects_status_mismatches() -> None:
    repository = InMemoryTransactionRepository()
    pending = seed_transaction(repository, "pending", "ws_CO_PENDING")
    completed = seed_transaction(repository, "completed", "ws_CO_COMPLETED")
    failed = seed_transaction(repository, "failed", "ws_CO_FAILED")
    provider = FakePaymentProvider(
        {
            pending.checkout_request_id: "completed",
            completed.checkout_request_id: "failed",
            failed.checkout_request_id: "completed",
        }
    )
    service = ReconciliationService(
        transaction_repository=repository,
        payment_provider=provider,
        stale_pending_minutes=15,
    )

    summary = service.run_reconciliation()

    assert summary.checked_transactions == 3
    assert summary.pending_local_but_provider_completed == 1
    assert summary.completed_local_but_provider_failed == 1
    assert summary.failed_local_but_provider_completed == 1
    assert provider.calls == ["ws_CO_PENDING", "ws_CO_COMPLETED", "ws_CO_FAILED"]


def test_reconciliation_detects_stale_pending_transaction() -> None:
    repository = InMemoryTransactionRepository()
    transaction = seed_transaction(repository, "pending", "ws_CO_STALE")
    old_transaction = transaction.model_copy(
        update={"created_at": datetime.now(UTC) - timedelta(minutes=30)}
    )
    repository._transactions[old_transaction.transaction_id] = old_transaction
    service = ReconciliationService(
        transaction_repository=repository,
        payment_provider=FakePaymentProvider({"ws_CO_STALE": "completed"}),
        stale_pending_minutes=15,
    )

    summary = service.run_reconciliation()

    assert summary.stale_pending_transaction == 1
    assert any(
        finding.finding_type == "stale_pending_transaction"
        for finding in summary.findings
    )


def test_reconciliation_detects_unknown_provider_state() -> None:
    repository = InMemoryTransactionRepository()
    seed_transaction(repository, "pending", "ws_CO_UNKNOWN")
    service = ReconciliationService(
        transaction_repository=repository,
        payment_provider=FakePaymentProvider({"ws_CO_UNKNOWN": "query_accepted"}),
        stale_pending_minutes=15,
    )

    summary = service.run_reconciliation()

    assert summary.provider_status_unknown == 1
    assert summary.findings[0].provider_status == "query_accepted"


class FakePaymentProvider:
    """Payment provider fake for reconciliation tests."""

    def __init__(self, statuses: dict[str, str]) -> None:
        self._statuses = statuses
        self.calls: list[str] = []

    def initiate_payment(
        self,
        *,
        phone_number: str,
        amount: int,
        account_reference: str,
        description: str,
    ) -> PaymentInitiationResponse:
        raise AssertionError("Reconciliation must not initiate payments.")

    def check_transaction_status(self, transaction_reference: str) -> PaymentStatusResponse:
        self.calls.append(transaction_reference)
        status = self._statuses.get(transaction_reference, "unknown")
        return PaymentStatusResponse(
            provider="daraja",
            rail="mpesa",
            checkout_request_id=transaction_reference,
            provider_transaction_id=transaction_reference,
            provider_reference=transaction_reference,
            result_code="0" if status == "completed" else "1",
            result_description=status,
            status=status,
        )


def seed_transaction(
    repository: InMemoryTransactionRepository,
    status: str,
    checkout_request_id: str,
) -> PendingTransaction:
    transaction = repository.save_pending_transaction(
        phone_number="254700000000",
        amount=1_000,
        account_reference=f"INV-{checkout_request_id}",
        description="Invoice payment",
        checkout_request_id=checkout_request_id,
        merchant_request_id=f"mock_{checkout_request_id}",
        provider_transaction_id=checkout_request_id,
        provider_reference=f"mock_{checkout_request_id}",
    )
    if status == "pending":
        return transaction

    updated = repository.update_transaction_status(
        checkout_request_id=checkout_request_id,
        status=status,
        result_code=0 if status == "completed" else 1,
        result_description=status,
    )
    assert updated is not None
    return updated
