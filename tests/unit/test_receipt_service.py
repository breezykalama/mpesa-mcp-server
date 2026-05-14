"""Tests for receipt service."""

from __future__ import annotations

from app.audit.logger import InMemoryAuditLogger
from app.observability.metrics import InMemoryMetricsRecorder
from app.receipts.generator import ReceiptGenerator
from app.services.receipt_service import ReceiptService
from app.storage.repositories import InMemoryTransactionRepository


def build_service() -> tuple[ReceiptService, InMemoryTransactionRepository, InMemoryAuditLogger]:
    repository = InMemoryTransactionRepository()
    audit_logger = InMemoryAuditLogger()
    service = ReceiptService(
        transaction_repository=repository,
        receipt_generator=ReceiptGenerator(),
        audit_logger=audit_logger,
        metrics_recorder=InMemoryMetricsRecorder(),
    )
    return service, repository, audit_logger


def seed_transaction(
    repository: InMemoryTransactionRepository,
    *,
    checkout_request_id: str = "ws_CO_123",
    status: str = "completed",
    mpesa_receipt_number: str | None = "RCP123",
) -> None:
    repository.save_pending_transaction(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-001",
        description="Invoice payment",
        checkout_request_id=checkout_request_id,
        merchant_request_id="mock_123",
    )
    repository.update_transaction_status(
        checkout_request_id=checkout_request_id,
        status=status,
        result_code=0 if status == "completed" else 1032,
        result_description="Callback processed.",
        mpesa_receipt_number=mpesa_receipt_number,
    )


def test_receipt_generated_for_completed_transaction() -> None:
    service, repository, _audit_logger = build_service()
    seed_transaction(repository)

    response = service.generate_receipt("ws_CO_123")

    assert response.status == "generated"
    assert response.allowed is True
    assert response.receipt is not None
    assert response.receipt["receipt_id"]
    assert response.receipt["checkout_request_id"] == "ws_CO_123"
    assert response.receipt["mpesa_receipt_number"] == "RCP123"
    assert response.receipt["issued_at"]


def test_pending_transaction_blocked() -> None:
    service, repository, _audit_logger = build_service()
    repository.save_pending_transaction(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-001",
        description="Invoice payment",
        checkout_request_id="ws_CO_PENDING",
        merchant_request_id="mock_123",
    )

    response = service.generate_receipt("ws_CO_PENDING")

    assert response.status == "blocked"
    assert response.allowed is False
    assert response.receipt is None


def test_failed_transaction_blocked() -> None:
    service, repository, _audit_logger = build_service()
    seed_transaction(repository, checkout_request_id="ws_CO_FAILED", status="failed")

    response = service.generate_receipt("ws_CO_FAILED")

    assert response.status == "blocked"
    assert response.allowed is False
    assert response.receipt is None


def test_transaction_not_found() -> None:
    service, _repository, _audit_logger = build_service()

    response = service.generate_receipt("missing")

    assert response.status == "not_found"
    assert response.allowed is False
    assert response.receipt is None


def test_missing_checkout_request_id() -> None:
    service, _repository, _audit_logger = build_service()

    response = service.generate_receipt("")

    assert response.status == "blocked"
    assert response.allowed is False
    assert "Checkout request ID is required" in response.reason


def test_audit_event_written() -> None:
    service, repository, audit_logger = build_service()
    seed_transaction(repository)

    response = service.generate_receipt("ws_CO_123")

    assert response.status == "generated"
    assert len(audit_logger.events) == 1
    event = audit_logger.events[0]
    assert event.event_type == "receipt_generated"
    assert event.payload["checkout_request_id"] == "ws_CO_123"
    assert response.receipt is not None
    assert event.payload["receipt_id"] == response.receipt["receipt_id"]
