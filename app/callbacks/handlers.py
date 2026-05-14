"""Callback handlers."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.audit.logger import AuditLoggerProtocol
from app.observability.metrics import MetricsRecorder
from app.storage.repositories import PendingTransaction, TransactionRepositoryProtocol


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

        callback = self._extract_callback(payload)
        checkout_request_id = self._get_string(callback, "CheckoutRequestID")
        if checkout_request_id is None:
            return CallbackProcessingResult(
                status="invalid",
                success=False,
                reason="CheckoutRequestID is required.",
            )

        result_code = self._get_int(callback, "ResultCode") or 0
        result_description = self._get_string(callback, "ResultDesc") or ""
        metadata = self._extract_metadata(callback)
        status = "completed" if result_code == 0 else "failed"

        updated_transaction = self._transaction_repository.update_transaction_status(
            checkout_request_id=checkout_request_id,
            status=status,
            result_code=result_code,
            result_description=result_description,
            mpesa_receipt_number=self._get_string(metadata, "MpesaReceiptNumber"),
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
        return result

    def _extract_callback(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = payload.get("Body")
        if isinstance(body, dict):
            callback = body.get("stkCallback")
            if isinstance(callback, dict):
                return callback

        callback = payload.get("stkCallback")
        if isinstance(callback, dict):
            return callback

        return payload

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
