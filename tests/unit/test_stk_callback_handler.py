"""Tests for STK callback handling."""

from __future__ import annotations

from typing import Any

from app.audit.logger import InMemoryAuditLogger
from app.callbacks.handlers import StkCallbackHandler
from app.observability.metrics import InMemoryMetricsRecorder
from app.storage.repositories import InMemoryTransactionRepository


def build_handler() -> tuple[
    StkCallbackHandler,
    InMemoryTransactionRepository,
    InMemoryAuditLogger,
]:
    repository = InMemoryTransactionRepository()
    audit_logger = InMemoryAuditLogger()
    handler = StkCallbackHandler(
        transaction_repository=repository,
        audit_logger=audit_logger,
        metrics_recorder=InMemoryMetricsRecorder(),
    )
    return handler, repository, audit_logger


def stk_callback_payload(
    *,
    checkout_request_id: str = "ws_CO_123",
    result_code: int = 0,
    result_description: str = "The service request is processed successfully.",
    metadata_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "Body": {
            "stkCallback": {
                "CheckoutRequestID": checkout_request_id,
                "ResultCode": result_code,
                "ResultDesc": result_description,
                "CallbackMetadata": {
                    "Item": metadata_items
                    or [
                        {"Name": "Amount", "Value": 1_000},
                        {"Name": "MpesaReceiptNumber", "Value": "RCP123"},
                        {"Name": "PhoneNumber", "Value": 254700000000},
                    ]
                },
            }
        }
    }


def seed_transaction(
    repository: InMemoryTransactionRepository,
    checkout_request_id: str = "ws_CO_123",
) -> None:
    repository.save_pending_transaction(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-001",
        description="Invoice payment",
        checkout_request_id=checkout_request_id,
        merchant_request_id="mock_123",
    )


def test_successful_callback_marks_transaction_completed() -> None:
    handler, repository, _audit_logger = build_handler()
    seed_transaction(repository)

    result = handler.process(stk_callback_payload())
    transaction = repository.find_by_checkout_request_id("ws_CO_123")

    assert result.status == "completed"
    assert result.success is True
    assert transaction is not None
    assert transaction.status == "completed"
    assert transaction.result_code == 0


def test_failed_callback_marks_transaction_failed() -> None:
    handler, repository, _audit_logger = build_handler()
    seed_transaction(repository)

    result = handler.process(
        stk_callback_payload(
            result_code=1032,
            result_description="Request cancelled by user.",
            metadata_items=[],
        )
    )
    transaction = repository.find_by_checkout_request_id("ws_CO_123")

    assert result.status == "failed"
    assert result.success is False
    assert transaction is not None
    assert transaction.status == "failed"
    assert transaction.result_code == 1032


def test_missing_checkout_request_id_returns_invalid_result() -> None:
    handler, _repository, _audit_logger = build_handler()

    result = handler.process({"Body": {"stkCallback": {"ResultCode": 0}}})

    assert result.status == "invalid"
    assert result.success is False
    assert "CheckoutRequestID is required" in result.reason


def test_receipt_number_is_stored_when_present() -> None:
    handler, repository, _audit_logger = build_handler()
    seed_transaction(repository)

    result = handler.process(stk_callback_payload())
    transaction = repository.find_by_checkout_request_id("ws_CO_123")

    assert result.mpesa_receipt_number == "RCP123"
    assert transaction is not None
    assert transaction.mpesa_receipt_number == "RCP123"


def test_audit_event_written() -> None:
    handler, repository, audit_logger = build_handler()
    seed_transaction(repository)

    result = handler.process(stk_callback_payload())

    assert result.status == "completed"
    assert len(audit_logger.events) == 1
    event = audit_logger.events[0]
    assert event.event_type == "stk_callback_processed"
    assert event.payload["checkout_request_id"] == "ws_CO_123"
    assert event.payload["status"] == "completed"
