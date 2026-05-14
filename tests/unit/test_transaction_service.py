"""Tests for transaction status service."""

from __future__ import annotations

from app.audit.logger import InMemoryAuditLogger
from app.daraja.client import MockDarajaClient
from app.observability.metrics import InMemoryMetricsRecorder
from app.safety.policy import PaymentPolicy
from app.services.transaction_service import TransactionService
from app.storage.repositories import InMemoryTransactionRepository


def build_service() -> tuple[
    TransactionService,
    InMemoryTransactionRepository,
    InMemoryAuditLogger,
]:
    repository = InMemoryTransactionRepository()
    audit_logger = InMemoryAuditLogger()
    service = TransactionService(
        policy=PaymentPolicy(max_stk_amount=10_000),
        daraja_client=MockDarajaClient(),
        transaction_repository=repository,
        audit_logger=audit_logger,
        metrics_recorder=InMemoryMetricsRecorder(),
    )
    return service, repository, audit_logger


def test_status_check_succeeds() -> None:
    service, _repository, _audit_logger = build_service()

    response = service.check_transaction_status("ws_CO_123")

    assert response.status == "completed"
    assert response.allowed is True
    assert response.checkout_request_id == "ws_CO_123"
    assert response.result_code == "0"


def test_missing_checkout_request_id_blocked() -> None:
    service, _repository, audit_logger = build_service()

    response = service.check_transaction_status("")

    assert response.status == "blocked"
    assert response.allowed is False
    assert "Checkout request ID is required" in response.reason
    assert audit_logger.events == []


def test_read_only_policy_allows_status_check() -> None:
    service, _repository, _audit_logger = build_service()

    response = service.check_transaction_status("ws_CO_456")

    assert response.allowed is True
    assert response.status == "completed"


def test_local_transaction_is_included_if_found() -> None:
    service, repository, _audit_logger = build_service()
    transaction = repository.save_pending_transaction(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-001",
        description="Invoice payment",
        checkout_request_id="ws_CO_789",
        merchant_request_id="mock_123",
    )

    response = service.check_transaction_status("ws_CO_789")

    assert response.local_transaction is not None
    assert response.local_transaction["transaction_id"] == transaction.transaction_id
    assert response.local_transaction["checkout_request_id"] == "ws_CO_789"


def test_audit_log_written() -> None:
    service, _repository, audit_logger = build_service()

    response = service.check_transaction_status("ws_CO_999")

    assert response.allowed is True
    assert len(audit_logger.events) == 1
    event = audit_logger.events[0]
    assert event.event_type == "transaction_status_checked"
    assert event.payload["checkout_request_id"] == "ws_CO_999"
    assert event.payload["status"] == "completed"
