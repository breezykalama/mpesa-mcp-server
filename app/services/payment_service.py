"""Payment service layer."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from pydantic import BaseModel

from app.approvals.models import ApprovalRequest
from app.approvals.service import ApprovalService
from app.audit.logger import AuditLoggerProtocol
from app.observability.metrics import MetricsRecorder
from app.payments.providers import PaymentInitiationResponse, PaymentProviderProtocol
from app.safety.policy import PaymentActionRequest, PaymentPolicy
from app.storage.repositories import PendingTransaction, TransactionRepositoryProtocol

logger = logging.getLogger(__name__)


class PaymentResponse(BaseModel):
    """Structured payment service response."""

    status: str
    allowed: bool
    reason: str
    requires_approval: bool = False
    approval_id: str | None = None
    transaction_id: str | None = None
    checkout_request_id: str | None = None
    merchant_request_id: str | None = None
    idempotency_key: str | None = None
    response_code: str | None = None
    response_description: str | None = None


class ApprovalExecutionResponse(BaseModel):
    """Result of approving and executing a payment request."""

    status: str
    allowed: bool
    reason: str
    approval: ApprovalRequest | None = None
    payment: PaymentResponse | None = None


class PaymentService:
    """Coordinate payment safety checks, provider calls, persistence, and audit logging."""

    def __init__(
        self,
        *,
        policy: PaymentPolicy,
        payment_provider: PaymentProviderProtocol,
        transaction_repository: TransactionRepositoryProtocol,
        audit_logger: AuditLoggerProtocol,
        approval_service: ApprovalService,
        metrics_recorder: MetricsRecorder,
    ) -> None:
        self._policy = policy
        self._payment_provider = payment_provider
        self._transaction_repository = transaction_repository
        self._audit_logger = audit_logger
        self._approval_service = approval_service
        self._metrics_recorder = metrics_recorder

    def initiate_stk_push(
        self,
        *,
        phone_number: str | None,
        amount: int | None,
        account_reference: str,
        description: str,
        idempotency_key: str | None = None,
    ) -> PaymentResponse:
        """Initiate an STK push using injected dependencies."""

        policy_decision = self._policy.evaluate(
            PaymentActionRequest(
                action="initiate_stk_push",
                amount=amount,
                phone_number=phone_number,
            )
        )

        if policy_decision.requires_approval:
            if phone_number is None or amount is None:
                logger.info(
                    "Payment blocked.",
                    extra={"event_type": "payment_blocked", "status": "blocked"},
                )
                return PaymentResponse(
                    status="blocked",
                    allowed=False,
                    reason="STK push requires phone number and amount.",
                )

            resolved_idempotency_key = idempotency_key or self._derive_idempotency_key(
                phone_number=phone_number,
                amount=amount,
                account_reference=account_reference,
                description=description,
            )
            approval = self._approval_service.create_approval_request(
                action="initiate_stk_push",
                payload={
                    "phone_number": phone_number,
                    "amount": amount,
                    "account_reference": account_reference,
                    "description": description,
                    "idempotency_key": resolved_idempotency_key,
                },
                reason=policy_decision.reason,
            )
            logger.info(
                "Payment requires approval.",
                extra={
                    "event_type": "approval_created",
                    "approval_id": approval.approval_id,
                    "amount": amount,
                    "status": policy_decision.status,
                },
            )
            self._metrics_recorder.increment("approval_required_count")
            return PaymentResponse(
                status=policy_decision.status,
                allowed=policy_decision.allowed,
                reason=policy_decision.reason,
                requires_approval=True,
                approval_id=approval.approval_id,
                idempotency_key=resolved_idempotency_key,
            )

        if not policy_decision.allowed:
            logger.info(
                "Payment blocked by policy.",
                extra={
                    "event_type": "payment_blocked",
                    "status": policy_decision.status,
                },
            )
            return PaymentResponse(
                status=policy_decision.status,
                allowed=False,
                reason=policy_decision.reason,
            )

        if phone_number is None or amount is None:
            logger.info(
                "Payment blocked.",
                extra={"event_type": "payment_blocked", "status": "blocked"},
            )
            return PaymentResponse(
                status="blocked",
                allowed=False,
                reason="STK push requires phone number and amount.",
            )

        resolved_idempotency_key = idempotency_key or self._derive_idempotency_key(
            phone_number=phone_number,
            amount=amount,
            account_reference=account_reference,
            description=description,
        )

        return self._execute_stk_push(
            phone_number=phone_number,
            amount=amount,
            account_reference=account_reference,
            description=description,
            idempotency_key=resolved_idempotency_key,
        )

    def execute_approved_payment(self, approval_id: str) -> ApprovalExecutionResponse:
        """Approve and execute a pending STK push approval request."""

        approval = self._approval_service.get_approval_request(approval_id)
        if approval is None:
            logger.info(
                "Approval execution failed; request not found.",
                extra={
                    "event_type": "approval_execution_blocked",
                    "approval_id": approval_id,
                    "status": "not_found",
                },
            )
            return ApprovalExecutionResponse(
                status="not_found",
                allowed=False,
                reason="Approval request was not found.",
            )

        if approval.status != "pending":
            logger.info(
                "Approval execution blocked.",
                extra={
                    "event_type": "approval_execution_blocked",
                    "approval_id": approval_id,
                    "status": approval.status,
                },
            )
            return ApprovalExecutionResponse(
                status="blocked",
                allowed=False,
                reason="Approval request is not pending.",
                approval=approval,
            )

        approval_response = self._approval_service.approve_request(approval_id)
        approved_approval = approval_response.approval
        if approved_approval is None:
            return ApprovalExecutionResponse(
                status="not_found",
                allowed=False,
                reason="Approval request was not found.",
            )

        payment_response = self._execute_approved_stk_push(approved_approval.payload)
        logger.info(
            "Approval executed.",
            extra={
                "event_type": "approval_executed",
                "approval_id": approval_id,
                "status": payment_response.status,
            },
        )

        return ApprovalExecutionResponse(
            status=approval_response.status,
            allowed=payment_response.allowed,
            reason="Approval request approved and payment execution attempted.",
            approval=approved_approval,
            payment=payment_response,
        )

    def _execute_approved_stk_push(self, payload: dict[str, Any]) -> PaymentResponse:
        phone_number = payload.get("phone_number")
        amount = payload.get("amount")
        account_reference = payload.get("account_reference")
        description = payload.get("description")
        idempotency_key = payload.get("idempotency_key")

        if (
            not isinstance(phone_number, str)
            or not isinstance(amount, int)
            or not isinstance(account_reference, str)
            or not isinstance(description, str)
            or not isinstance(idempotency_key, str)
        ):
            logger.info(
                "Approval payload blocked.",
                extra={"event_type": "approval_execution_blocked", "status": "blocked"},
            )
            return PaymentResponse(
                status="blocked",
                allowed=False,
                reason="Approval payload is not executable.",
            )

        return self._execute_stk_push(
            phone_number=phone_number,
            amount=amount,
            account_reference=account_reference,
            description=description,
            idempotency_key=idempotency_key,
        )

    def _execute_stk_push(
        self,
        *,
        phone_number: str,
        amount: int,
        account_reference: str,
        description: str,
        idempotency_key: str,
    ) -> PaymentResponse:
        existing_transaction = self._transaction_repository.find_by_idempotency_key(
            idempotency_key
        )
        if existing_transaction is not None:
            logger.info(
                "Existing payment returned for idempotency key.",
                extra={
                    "event_type": "payment_idempotency_hit",
                    "transaction_id": existing_transaction.transaction_id,
                    "status": existing_transaction.status,
                },
            )
            return self._response_from_existing_transaction(existing_transaction)

        try:
            logger.info(
                "Payment initiation started.",
                extra={"event_type": "payment_initiation_started", "amount": amount},
            )
            provider_response = self._payment_provider.initiate_payment(
                phone_number=phone_number,
                amount=amount,
                account_reference=account_reference,
                description=description,
            )
        except Exception as exc:
            logger.exception(
                "Payment initiation failed.",
                extra={"event_type": "payment_initiation_failed", "status": "failed"},
            )
            self._metrics_recorder.increment("failed_payment_count")
            return PaymentResponse(
                status="failed",
                allowed=False,
                reason=f"Payment provider initiation failed: {exc}",
            )

        transaction = self._transaction_repository.save_pending_transaction(
            phone_number=phone_number,
            amount=amount,
            account_reference=account_reference,
            description=description,
            checkout_request_id=provider_response.checkout_request_id,
            merchant_request_id=provider_response.merchant_request_id,
            idempotency_key=idempotency_key,
            provider=provider_response.provider,
            rail=provider_response.rail,
            provider_transaction_id=provider_response.provider_transaction_id,
            provider_reference=provider_response.provider_reference,
        )

        self._log_success(transaction, provider_response)
        logger.info(
            "Payment initiated.",
            extra={
                "event_type": "payment_initiated",
                "transaction_id": transaction.transaction_id,
                "amount": amount,
                "status": transaction.status,
            },
        )
        self._metrics_recorder.increment("successful_payment_count")

        return PaymentResponse(
            status="pending",
            allowed=True,
            reason="STK push initiated successfully.",
            transaction_id=transaction.transaction_id,
            checkout_request_id=provider_response.checkout_request_id,
            merchant_request_id=provider_response.merchant_request_id,
            idempotency_key=idempotency_key,
            response_code=provider_response.response_code,
            response_description=provider_response.response_description,
        )

    def _derive_idempotency_key(
        self,
        *,
        phone_number: str,
        amount: int,
        account_reference: str,
        description: str,
    ) -> str:
        raw_key = f"{phone_number}|{amount}|{account_reference}|{description}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    def _response_from_existing_transaction(
        self,
        transaction: PendingTransaction,
    ) -> PaymentResponse:
        return PaymentResponse(
            status=transaction.status,
            allowed=True,
            reason="Existing transaction returned for idempotency key.",
            transaction_id=transaction.transaction_id,
            checkout_request_id=transaction.checkout_request_id,
            merchant_request_id=transaction.merchant_request_id,
            idempotency_key=transaction.idempotency_key,
        )

    def _log_success(
        self,
        transaction: PendingTransaction,
        provider_response: PaymentInitiationResponse,
    ) -> None:
        self._audit_logger.log_event(
            "stk_push_initiated",
            {
                "transaction_id": transaction.transaction_id,
                "amount": transaction.amount,
                "phone_number": transaction.phone_number,
                "idempotency_key": transaction.idempotency_key,
                "checkout_request_id": provider_response.checkout_request_id,
                "merchant_request_id": provider_response.merchant_request_id,
            },
        )
