"""MCP tool definitions."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import ValidationError

from app.analytics.service import DailyAnalyticsSummary
from app.approvals.service import ApprovalServiceResponse
from app.mcp.schemas import (
    ApprovePaymentRequestInput,
    CheckTransactionStatusInput,
    GenerateReceiptInput,
    GetFailedTransactionsInput,
    GetTodaySummaryInput,
    InitiateStkPushInput,
    McpToolResponse,
    RejectPaymentRequestInput,
)
from app.services.payment_service import ApprovalExecutionResponse, PaymentResponse
from app.services.receipt_service import ReceiptServiceResponse
from app.services.transaction_service import TransactionStatusServiceResponse
from app.storage.repositories import PendingTransaction


class InitiateStkPushServiceProtocol(Protocol):
    """Service contract required by the initiate_stk_push MCP tool."""

    def initiate_stk_push(
        self,
        *,
        phone_number: str | None,
        amount: int | None,
        account_reference: str,
        description: str,
        idempotency_key: str | None = None,
    ) -> PaymentResponse:
        """Initiate an STK push payment."""


class CheckTransactionStatusServiceProtocol(Protocol):
    """Service contract required by the check_transaction_status MCP tool."""

    def check_transaction_status(
        self,
        checkout_request_id: str,
    ) -> TransactionStatusServiceResponse:
        """Check transaction status."""


class GenerateReceiptServiceProtocol(Protocol):
    """Service contract required by the generate_receipt MCP tool."""

    def generate_receipt(self, checkout_request_id: str) -> ReceiptServiceResponse:
        """Generate a receipt."""


class AnalyticsServiceProtocol(Protocol):
    """Service contract required by analytics MCP tools."""

    def get_today_summary(self) -> DailyAnalyticsSummary:
        """Return today's analytics summary."""

    def get_failed_transactions(self) -> list[PendingTransaction]:
        """Return failed transactions."""


class ApprovalExecutionServiceProtocol(Protocol):
    """Service contract required by approval execution MCP tools."""

    def execute_approved_payment(self, approval_id: str) -> ApprovalExecutionResponse:
        """Approve and execute a payment request."""


class ApprovalRejectionServiceProtocol(Protocol):
    """Service contract required by approval rejection MCP tools."""

    def reject_request(self, approval_id: str) -> ApprovalServiceResponse:
        """Reject an approval request."""


def initiate_stk_push_tool(
    input_data: InitiateStkPushInput | dict[str, Any],
    payment_service: InitiateStkPushServiceProtocol,
) -> McpToolResponse:
    """Validate MCP input and delegate STK push initiation to the payment service."""

    try:
        tool_input = _validate_input(input_data)
    except ValidationError as exc:
        return McpToolResponse(
            status="invalid_input",
            allowed=False,
            reason="Invalid initiate_stk_push input.",
            errors=[error["msg"] for error in exc.errors()],
        )

    payment_response = payment_service.initiate_stk_push(
        phone_number=tool_input.phone_number,
        amount=tool_input.amount,
        account_reference=tool_input.account_reference,
        description=tool_input.description,
        idempotency_key=tool_input.idempotency_key,
    )

    return McpToolResponse(
        status=payment_response.status,
        allowed=payment_response.allowed,
        reason=payment_response.reason,
        requires_approval=payment_response.requires_approval,
        data=payment_response.model_dump(
            exclude={"status", "allowed", "reason", "requires_approval"},
            exclude_none=True,
        ),
    )


def check_transaction_status_tool(
    input_data: CheckTransactionStatusInput | dict[str, Any],
    transaction_service: CheckTransactionStatusServiceProtocol,
) -> McpToolResponse:
    """Validate MCP input and delegate transaction status checks to the service."""

    try:
        tool_input = _validate_transaction_status_input(input_data)
    except ValidationError as exc:
        return McpToolResponse(
            status="invalid_input",
            allowed=False,
            reason="Invalid check_transaction_status input.",
            errors=[error["msg"] for error in exc.errors()],
        )

    transaction_response = transaction_service.check_transaction_status(
        tool_input.checkout_request_id
    )

    return McpToolResponse(
        status=transaction_response.status,
        allowed=transaction_response.allowed,
        reason=transaction_response.reason,
        data=transaction_response.model_dump(
            exclude={"status", "allowed", "reason", "errors"},
            exclude_none=True,
        ),
        errors=transaction_response.errors,
    )


def generate_receipt_tool(
    input_data: GenerateReceiptInput | dict[str, Any],
    receipt_service: GenerateReceiptServiceProtocol,
) -> McpToolResponse:
    """Validate MCP input and delegate receipt generation to the service."""

    try:
        tool_input = _validate_generate_receipt_input(input_data)
    except ValidationError as exc:
        return McpToolResponse(
            status="invalid_input",
            allowed=False,
            reason="Invalid generate_receipt input.",
            errors=[error["msg"] for error in exc.errors()],
        )

    receipt_response = receipt_service.generate_receipt(tool_input.checkout_request_id)

    return McpToolResponse(
        status=receipt_response.status,
        allowed=receipt_response.allowed,
        reason=receipt_response.reason,
        data=receipt_response.model_dump(
            exclude={"status", "allowed", "reason"},
            exclude_none=True,
        ),
    )


def get_today_summary_tool(
    input_data: GetTodaySummaryInput | dict[str, Any],
    analytics_service: AnalyticsServiceProtocol,
) -> McpToolResponse:
    """Validate MCP input and delegate today's summary to the analytics service."""

    try:
        _validate_today_summary_input(input_data)
    except ValidationError as exc:
        return McpToolResponse(
            status="invalid_input",
            allowed=False,
            reason="Invalid get_today_summary input.",
            errors=[error["msg"] for error in exc.errors()],
        )

    summary = analytics_service.get_today_summary()

    return McpToolResponse(
        status="ok",
        allowed=True,
        reason="Today's summary retrieved successfully.",
        data={"summary": summary.model_dump(mode="json")},
    )


def get_failed_transactions_tool(
    input_data: GetFailedTransactionsInput | dict[str, Any],
    analytics_service: AnalyticsServiceProtocol,
) -> McpToolResponse:
    """Validate MCP input and delegate failed transaction lookup to analytics service."""

    try:
        _validate_failed_transactions_input(input_data)
    except ValidationError as exc:
        return McpToolResponse(
            status="invalid_input",
            allowed=False,
            reason="Invalid get_failed_transactions input.",
            errors=[error["msg"] for error in exc.errors()],
        )

    failed_transactions = analytics_service.get_failed_transactions()

    return McpToolResponse(
        status="ok",
        allowed=True,
        reason="Failed transactions retrieved successfully.",
        data={
            "transactions": [
                transaction.model_dump(mode="json") for transaction in failed_transactions
            ]
        },
    )


def approve_payment_request_tool(
    input_data: ApprovePaymentRequestInput | dict[str, Any],
    payment_service: ApprovalExecutionServiceProtocol,
) -> McpToolResponse:
    """Validate MCP input and delegate approval execution to the payment service."""

    try:
        tool_input = _validate_approve_payment_request_input(input_data)
    except ValidationError as exc:
        return McpToolResponse(
            status="invalid_input",
            allowed=False,
            reason="Invalid approve_payment_request input.",
            errors=[error["msg"] for error in exc.errors()],
        )

    execution_response = payment_service.execute_approved_payment(tool_input.approval_id)
    return _approval_execution_response_to_mcp_response(execution_response)


def reject_payment_request_tool(
    input_data: RejectPaymentRequestInput | dict[str, Any],
    approval_service: ApprovalRejectionServiceProtocol,
) -> McpToolResponse:
    """Validate MCP input and delegate rejection to the approval service."""

    try:
        tool_input = _validate_reject_payment_request_input(input_data)
    except ValidationError as exc:
        return McpToolResponse(
            status="invalid_input",
            allowed=False,
            reason="Invalid reject_payment_request input.",
            errors=[error["msg"] for error in exc.errors()],
        )

    approval_response = approval_service.reject_request(tool_input.approval_id)
    return _approval_response_to_mcp_response(approval_response)


def _validate_input(input_data: InitiateStkPushInput | dict[str, Any]) -> InitiateStkPushInput:
    if isinstance(input_data, InitiateStkPushInput):
        return input_data

    return InitiateStkPushInput.model_validate(input_data)


def _validate_transaction_status_input(
    input_data: CheckTransactionStatusInput | dict[str, Any],
) -> CheckTransactionStatusInput:
    if isinstance(input_data, CheckTransactionStatusInput):
        return input_data

    return CheckTransactionStatusInput.model_validate(input_data)


def _validate_generate_receipt_input(
    input_data: GenerateReceiptInput | dict[str, Any],
) -> GenerateReceiptInput:
    if isinstance(input_data, GenerateReceiptInput):
        return input_data

    return GenerateReceiptInput.model_validate(input_data)


def _validate_today_summary_input(
    input_data: GetTodaySummaryInput | dict[str, Any],
) -> GetTodaySummaryInput:
    if isinstance(input_data, GetTodaySummaryInput):
        return input_data

    return GetTodaySummaryInput.model_validate(input_data)


def _validate_failed_transactions_input(
    input_data: GetFailedTransactionsInput | dict[str, Any],
) -> GetFailedTransactionsInput:
    if isinstance(input_data, GetFailedTransactionsInput):
        return input_data

    return GetFailedTransactionsInput.model_validate(input_data)


def _validate_approve_payment_request_input(
    input_data: ApprovePaymentRequestInput | dict[str, Any],
) -> ApprovePaymentRequestInput:
    if isinstance(input_data, ApprovePaymentRequestInput):
        return input_data

    return ApprovePaymentRequestInput.model_validate(input_data)


def _validate_reject_payment_request_input(
    input_data: RejectPaymentRequestInput | dict[str, Any],
) -> RejectPaymentRequestInput:
    if isinstance(input_data, RejectPaymentRequestInput):
        return input_data

    return RejectPaymentRequestInput.model_validate(input_data)


def _approval_response_to_mcp_response(response: ApprovalServiceResponse) -> McpToolResponse:
    return McpToolResponse(
        status=response.status,
        allowed=response.allowed,
        reason=response.reason,
        data=response.model_dump(
            mode="json",
            exclude={"status", "allowed", "reason"},
            exclude_none=True,
        ),
    )


def _approval_execution_response_to_mcp_response(
    response: ApprovalExecutionResponse,
) -> McpToolResponse:
    return McpToolResponse(
        status=response.status,
        allowed=response.allowed,
        reason=response.reason,
        data=response.model_dump(
            mode="json",
            exclude={"status", "allowed", "reason"},
            exclude_none=True,
        ),
    )
