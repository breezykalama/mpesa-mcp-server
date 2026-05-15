"""Transaction service layer."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from app.audit.logger import AuditLoggerProtocol
from app.observability.metrics import MetricsRecorder
from app.payments.providers import PaymentProviderProtocol
from app.safety.policy import PaymentActionRequest, PaymentPolicy
from app.storage.repositories import PendingTransaction, TransactionRepositoryProtocol

logger = logging.getLogger(__name__)


class TransactionStatusServiceResponse(BaseModel):
    """Structured transaction status service response."""

    status: str
    allowed: bool
    reason: str
    checkout_request_id: str | None = None
    provider: str | None = None
    rail: str | None = None
    provider_transaction_id: str | None = None
    provider_reference: str | None = None
    result_code: str | None = None
    result_description: str | None = None
    local_transaction: dict[str, Any] | None = None
    errors: list[str] = Field(default_factory=list)


class TransactionService:
    """Coordinate transaction status checks."""

    def __init__(
        self,
        *,
        policy: PaymentPolicy,
        payment_provider: PaymentProviderProtocol,
        transaction_repository: TransactionRepositoryProtocol,
        audit_logger: AuditLoggerProtocol,
        metrics_recorder: MetricsRecorder,
    ) -> None:
        self._policy = policy
        self._payment_provider = payment_provider
        self._transaction_repository = transaction_repository
        self._audit_logger = audit_logger
        self._metrics_recorder = metrics_recorder

    def check_transaction_status(
        self,
        checkout_request_id: str,
    ) -> TransactionStatusServiceResponse:
        """Check transaction status by checkout request ID."""

        self._metrics_recorder.increment("tool_call_count")

        if not checkout_request_id:
            logger.info(
                "Transaction status check blocked.",
                extra={
                    "event_type": "transaction_status_blocked",
                    "status": "blocked",
                },
            )
            return TransactionStatusServiceResponse(
                status="blocked",
                allowed=False,
                reason="Checkout request ID is required.",
            )

        policy_decision = self._policy.evaluate(
            PaymentActionRequest(action="check_transaction_status")
        )

        if not policy_decision.allowed:
            logger.info(
                "Transaction status check blocked by policy.",
                extra={
                    "event_type": "transaction_status_blocked",
                    "status": policy_decision.status,
                },
            )
            return TransactionStatusServiceResponse(
                status=policy_decision.status,
                allowed=False,
                reason=policy_decision.reason,
            )

        local_transaction = self._transaction_repository.find_by_checkout_request_id(
            checkout_request_id
        )
        logger.info(
            "Transaction status query started.",
            extra={"event_type": "transaction_status_query_started"},
        )
        provider_response = self._payment_provider.check_transaction_status(checkout_request_id)

        self._audit_logger.log_event(
            "transaction_status_checked",
            {
                "checkout_request_id": checkout_request_id,
                "status": provider_response.status,
                "local_transaction_id": (
                    local_transaction.transaction_id if local_transaction is not None else None
                ),
            },
        )

        logger.info(
            "Transaction status checked.",
            extra={
                "event_type": "transaction_status_checked",
                "status": provider_response.status,
            },
        )
        return TransactionStatusServiceResponse(
            status=provider_response.status,
            allowed=True,
            reason=provider_response.result_description,
            checkout_request_id=provider_response.checkout_request_id,
            provider=provider_response.provider,
            rail=provider_response.rail,
            provider_transaction_id=provider_response.provider_transaction_id,
            provider_reference=provider_response.provider_reference,
            result_code=provider_response.result_code,
            result_description=provider_response.result_description,
            local_transaction=self._dump_local_transaction(local_transaction),
        )

    def _dump_local_transaction(
        self,
        transaction: PendingTransaction | None,
    ) -> dict[str, Any] | None:
        if transaction is None:
            return None

        return transaction.model_dump()
