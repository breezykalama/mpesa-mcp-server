"""Receipt service layer."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.audit.logger import AuditLoggerProtocol
from app.observability.metrics import MetricsRecorder
from app.receipts.generator import ReceiptGenerator
from app.storage.repositories import TransactionRepositoryProtocol


class ReceiptServiceResponse(BaseModel):
    """Structured receipt service response."""

    status: str
    allowed: bool
    reason: str
    receipt: dict[str, Any] | None = None


class ReceiptService:
    """Coordinate receipt generation."""

    def __init__(
        self,
        *,
        transaction_repository: TransactionRepositoryProtocol,
        receipt_generator: ReceiptGenerator,
        audit_logger: AuditLoggerProtocol,
        metrics_recorder: MetricsRecorder,
    ) -> None:
        self._transaction_repository = transaction_repository
        self._receipt_generator = receipt_generator
        self._audit_logger = audit_logger
        self._metrics_recorder = metrics_recorder

    def generate_receipt(self, checkout_request_id: str) -> ReceiptServiceResponse:
        """Generate a receipt for a completed transaction."""

        if not checkout_request_id:
            return ReceiptServiceResponse(
                status="blocked",
                allowed=False,
                reason="Checkout request ID is required.",
            )

        transaction = self._transaction_repository.find_by_checkout_request_id(
            checkout_request_id
        )
        if transaction is None:
            return ReceiptServiceResponse(
                status="not_found",
                allowed=False,
                reason="Transaction was not found.",
            )

        generation_result = self._receipt_generator.generate(transaction)
        if generation_result.receipt is None:
            return ReceiptServiceResponse(
                status=generation_result.status,
                allowed=generation_result.allowed,
                reason=generation_result.reason,
            )

        receipt_payload = generation_result.receipt.model_dump(mode="json")
        self._audit_logger.log_event(
            "receipt_generated",
            {
                "receipt_id": generation_result.receipt.receipt_id,
                "transaction_id": transaction.transaction_id,
                "checkout_request_id": transaction.checkout_request_id,
            },
        )
        self._metrics_recorder.increment("receipt_generated_count")

        return ReceiptServiceResponse(
            status=generation_result.status,
            allowed=generation_result.allowed,
            reason=generation_result.reason,
            receipt=receipt_payload,
        )

    def generate_receipt_by_reference(self, reference: str) -> ReceiptServiceResponse:
        """Generate a receipt from a checkout or provider transaction reference."""

        if not reference:
            return ReceiptServiceResponse(
                status="blocked",
                allowed=False,
                reason="Transaction reference is required.",
            )

        transaction = self._transaction_repository.find_by_checkout_request_id(reference)
        if transaction is None:
            transaction = next(
                (
                    candidate
                    for candidate in self._transaction_repository.list_transactions()
                    if candidate.provider_transaction_id == reference
                    or candidate.provider_reference == reference
                ),
                None,
            )

        if transaction is None:
            return ReceiptServiceResponse(
                status="not_found",
                allowed=False,
                reason="Transaction was not found.",
            )

        return self.generate_receipt(transaction.checkout_request_id)
