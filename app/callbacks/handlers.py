"""Callback handlers."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from app.audit.logger import AuditLoggerProtocol
from app.observability.metrics import MetricsRecorder
from app.storage.repositories import PendingTransaction, TransactionRepositoryProtocol
from app.transactions.state_machine import (
    TransactionStateTransitionError,
    validate_transition,
)

logger = logging.getLogger(__name__)


class CallbackProcessingResult(BaseModel):
    """Structured result returned after processing an STK callback."""

    status: str
    success: bool
    reason: str
    checkout_request_id: str | None = None
    result_code: int | None = None
    result_description: str | None = None
    mpesa_receipt_number: str | None = None
    phone_number: str | None = None
    amount: int | None = None
    transaction_id: str | None = None


class StkCallbackHandler:
    """Process M-Pesa STK callback payloads."""

    def __init__(
        self,
        *,
        transaction_repository: TransactionRepositoryProtocol,
        audit_logger: AuditLoggerProtocol,
        metrics_recorder: MetricsRecorder,
    ) -> None:
        self._transaction_repository = transaction_repository
        self._audit_logger = audit_logger
        self._metrics_recorder = metrics_recorder

    def process(self, payload: dict[str, Any]) -> CallbackProcessingResult:
        """Process a raw STK callback payload."""

        self._metrics_recorder.increment("callback_received_count")
        logger.info(
            "STK callback received.",
            extra={"event_type": "callback_received"},
        )

        callback = self._extract_callback(payload)
        if callback is None:
            return self._invalid_payload_result()

        checkout_request_id = self._get_string(callback, "CheckoutRequestID")
        result_code = self._get_int(callback, "ResultCode")
        result_description = self._get_string(callback, "ResultDesc")
        if (
            checkout_request_id is None
            or result_code is None
            or result_description is None
        ):
            return self._invalid_payload_result(checkout_request_id=checkout_request_id)

        transaction = self._transaction_repository.find_by_checkout_request_id(
            checkout_request_id
        )
        if transaction is None:
            return self._unknown_transaction_result(checkout_request_id)

        metadata = self._extract_metadata(callback)
        if result_code == 0 and not self._has_success_metadata(metadata):
            return self._invalid_payload_result(checkout_request_id=checkout_request_id)

        status = "completed" if result_code == 0 else "failed"
        try:
            validate_transition(transaction.status, status)
        except TransactionStateTransitionError as exc:
            return self._invalid_transition_result(
                exc=exc,
                checkout_request_id=checkout_request_id,
                result_code=result_code,
                result_description=result_description,
                metadata=metadata,
            )

        callback_amount = self._get_int(metadata, "Amount")
        if callback_amount is not None and callback_amount != transaction.amount:
            return self._mismatch_result(
                event_type="stk_callback_amount_mismatch",
                status="amount_mismatch",
                reason="Callback amount does not match expected transaction amount.",
                checkout_request_id=checkout_request_id,
                result_code=result_code,
                result_description=result_description,
                amount=callback_amount,
                transaction_id=transaction.transaction_id,
            )

        callback_phone_number = self._get_string(metadata, "PhoneNumber")
        if callback_phone_number is not None and self._normalize_phone_number(
            callback_phone_number
        ) != self._normalize_phone_number(transaction.phone_number):
            return self._mismatch_result(
                event_type="stk_callback_phone_mismatch",
                status="phone_mismatch",
                reason="Callback phone number does not match expected transaction phone.",
                checkout_request_id=checkout_request_id,
                result_code=result_code,
                result_description=result_description,
                phone_number=callback_phone_number,
                transaction_id=transaction.transaction_id,
            )

        try:
            updated_transaction = self._transaction_repository.update_transaction_status(
                checkout_request_id=checkout_request_id,
                status=status,
                result_code=result_code,
                result_description=result_description,
                mpesa_receipt_number=self._get_string(metadata, "MpesaReceiptNumber"),
            )
        except TransactionStateTransitionError as exc:
            return self._invalid_transition_result(
                exc=exc,
                checkout_request_id=checkout_request_id,
                result_code=result_code,
                result_description=result_description,
                metadata=metadata,
            )

        result = CallbackProcessingResult(
            status=status,
            success=result_code == 0,
            reason=result_description,
            checkout_request_id=checkout_request_id,
            result_code=result_code,
            result_description=result_description,
            mpesa_receipt_number=self._get_string(metadata, "MpesaReceiptNumber"),
            phone_number=self._get_string(metadata, "PhoneNumber"),
            amount=self._get_int(metadata, "Amount"),
            transaction_id=self._transaction_id(updated_transaction),
        )
        self._log_callback(result)
        logger.info(
            "STK callback processed.",
            extra={
                "event_type": "callback_accepted",
                "status": result.status,
                "transaction_id": result.transaction_id,
                "amount": result.amount,
            },
        )
        return result

    def _extract_callback(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        body = payload.get("Body")
        if not isinstance(body, dict):
            return None
        callback = body.get("stkCallback")
        if not isinstance(callback, dict):
            return None
        return callback

    def _extract_metadata(self, callback: dict[str, Any]) -> dict[str, Any]:
        metadata = callback.get("CallbackMetadata")
        if not isinstance(metadata, dict):
            return {}

        items = metadata.get("Item")
        if not isinstance(items, list):
            return {}

        parsed: dict[str, Any] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("Name")
            if not isinstance(name, str) or "Value" not in item:
                continue
            parsed[name] = item["Value"]
        return parsed

    def _log_callback(self, result: CallbackProcessingResult) -> None:
        self._audit_logger.log_event(
            "stk_callback_processed",
            result.model_dump(exclude_none=True),
        )

    def _invalid_payload_result(
        self,
        *,
        checkout_request_id: str | None = None,
    ) -> CallbackProcessingResult:
        result = CallbackProcessingResult(
            status="invalid_callback",
            success=False,
            reason="Malformed callback payload.",
            checkout_request_id=checkout_request_id,
        )
        self._audit_logger.log_event(
            "stk_callback_invalid_payload",
            result.model_dump(exclude_none=True),
            actor="mpesa_callback",
        )
        logger.info(
            "STK callback rejected because payload is malformed.",
            extra={"event_type": "callback_invalid_payload", "status": "invalid"},
        )
        return result

    def _unknown_transaction_result(
        self,
        checkout_request_id: str,
    ) -> CallbackProcessingResult:
        result = CallbackProcessingResult(
            status="unknown_transaction",
            success=False,
            reason="Callback does not match a known transaction.",
            checkout_request_id=checkout_request_id,
        )
        self._audit_logger.log_event(
            "stk_callback_unknown_transaction",
            result.model_dump(exclude_none=True),
            actor="mpesa_callback",
        )
        logger.warning(
            "STK callback rejected for unknown transaction.",
            extra={
                "event_type": "callback_unknown_transaction",
                "checkout_request_id": checkout_request_id,
            },
        )
        return result

    def _mismatch_result(
        self,
        *,
        event_type: str,
        status: str,
        reason: str,
        checkout_request_id: str,
        result_code: int,
        result_description: str,
        amount: int | None = None,
        phone_number: str | None = None,
        transaction_id: str | None = None,
    ) -> CallbackProcessingResult:
        result = CallbackProcessingResult(
            status=status,
            success=False,
            reason=reason,
            checkout_request_id=checkout_request_id,
            result_code=result_code,
            result_description=result_description,
            amount=amount,
            phone_number=phone_number,
            transaction_id=transaction_id,
        )
        self._audit_logger.log_event(
            event_type,
            result.model_dump(exclude_none=True),
            actor="mpesa_callback",
        )
        logger.warning(
            "STK callback rejected because it did not match the transaction.",
            extra={"event_type": event_type, "checkout_request_id": checkout_request_id},
        )
        return result

    def _invalid_transition_result(
        self,
        *,
        exc: TransactionStateTransitionError,
        checkout_request_id: str,
        result_code: int,
        result_description: str,
        metadata: dict[str, Any],
    ) -> CallbackProcessingResult:
        result = CallbackProcessingResult(
            status="invalid_transition",
            success=False,
            reason=str(exc),
            checkout_request_id=checkout_request_id,
            result_code=result_code,
            result_description=result_description,
            mpesa_receipt_number=self._get_string(metadata, "MpesaReceiptNumber"),
            phone_number=self._get_string(metadata, "PhoneNumber"),
            amount=self._get_int(metadata, "Amount"),
        )
        self._audit_logger.log_event(
            "stk_callback_invalid_transition",
            result.model_dump(exclude_none=True),
        )
        logger.warning(
            "STK callback attempted invalid transaction transition.",
            extra={
                "event_type": "callback_invalid_transition",
                "checkout_request_id": checkout_request_id,
            },
        )
        return result

    def _has_success_metadata(self, metadata: dict[str, Any]) -> bool:
        return (
            self._get_int(metadata, "Amount") is not None
            and self._get_string(metadata, "MpesaReceiptNumber") is not None
            and self._get_string(metadata, "PhoneNumber") is not None
        )

    def _normalize_phone_number(self, phone_number: str) -> str:
        return "".join(character for character in phone_number if character.isdigit())

    def _get_string(self, source: dict[str, Any], key: str) -> str | None:
        value = source.get(key)
        if value is None:
            return None
        return str(value)

    def _get_int(self, source: dict[str, Any], key: str) -> int | None:
        value = source.get(key)
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _transaction_id(self, transaction: PendingTransaction | None) -> str | None:
        if transaction is None:
            return None
        return transaction.transaction_id
