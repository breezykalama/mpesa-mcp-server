"""MCP tool definitions."""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import wraps
from typing import Any, ParamSpec, Protocol, TypeVar

from pydantic import ValidationError

from app.analytics.service import DailyAnalyticsSummary
from app.infrastructure.health import InfrastructureUnavailableError
from app.mcp.schemas import (
    CheckPaymentStatusInput,
    CheckTransactionStatusInput,
    GenerateReceiptInput,
    GetFailedTransactionsInput,
    GetTodaySummaryInput,
    InitiatePaymentInput,
    InitiateStkPushInput,
    McpToolResponse,
    RunReconciliationInput,
)
from app.observability.tracing import correlation_context
from app.policy.tool_policy import ToolPolicyEngine
from app.rate_limit.limiter import RateLimiterProtocol
from app.reconciliation.service import ReconciliationSummary
from app.services.payment_service import PaymentResponse
from app.services.receipt_service import ReceiptServiceResponse
from app.services.transaction_service import TransactionStatusServiceResponse
from app.storage.repositories import PendingTransaction

logger = logging.getLogger(__name__)

MCP_AGENT_REQUEST_ORIGIN = "mcp_agent"
"""Request origin recorded for payment requests made by an MCP agent.

AI agents may request payments, but approval decisions are human-only. This module
intentionally exposes no tool that approves or rejects approval requests.
"""
P = ParamSpec("P")
R = TypeVar("R")


def with_correlation_context(func: Callable[P, R]) -> Callable[P, R]:  # noqa: UP047
    """Ensure MCP tool calls have an active correlation ID."""

    @wraps(func)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        with correlation_context():
            return func(*args, **kwargs)

    return wrapped


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
        request_origin: str = "unspecified",
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
class ReconciliationServiceProtocol(Protocol):
    """Service contract required by reconciliation MCP tools."""

    def run_reconciliation(self) -> ReconciliationSummary:
        """Run reconciliation."""


@with_correlation_context
def initiate_stk_push_tool(
    input_data: InitiateStkPushInput | dict[str, Any],
    payment_service: InitiateStkPushServiceProtocol,
    *,
    rate_limiter: RateLimiterProtocol | None = None,
    rate_limit_enabled: bool = False,
    rate_limit_window_seconds: int = 60,
    rate_limit_max_requests: int = 5,
    tool_policy: ToolPolicyEngine | None = None,
) -> McpToolResponse:
    """Validate MCP input and delegate payment initiation to the payment service.

    The public MCP tool remains M-Pesa-first for compatibility. The service layer
    delegates to the configured payment provider, which can be Daraja or another rail.
    """

    policy_response = _check_tool_policy("initiate_stk_push", tool_policy)
    if policy_response is not None:
        return policy_response

    try:
        tool_input = _validate_input(input_data)
    except ValidationError as exc:
        _log_tool_event("initiate_stk_push", "mcp_tool_invalid_input")
        return McpToolResponse(
            status="invalid_input",
            allowed=False,
            reason="Invalid initiate_stk_push input.",
            errors=[error["msg"] for error in exc.errors()],
        )

    rate_limit_response = _check_rate_limit(
        rate_limiter=rate_limiter,
        enabled=rate_limit_enabled,
        key=f"initiate_stk_push:{tool_input.phone_number}",
        limit=rate_limit_max_requests,
        window_seconds=rate_limit_window_seconds,
    )
    if rate_limit_response is not None:
        _log_tool_event("initiate_stk_push", "mcp_tool_rate_limited", status="rate_limited")
        return rate_limit_response

    _log_tool_event("initiate_stk_push", "mcp_tool_delegated")
    payment_response = payment_service.initiate_stk_push(
        phone_number=tool_input.phone_number,
        amount=tool_input.amount,
        account_reference=tool_input.account_reference,
        description=tool_input.description,
        idempotency_key=tool_input.idempotency_key,
        request_origin=MCP_AGENT_REQUEST_ORIGIN,
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


@with_correlation_context
def initiate_payment_tool(
    input_data: InitiatePaymentInput | dict[str, Any],
    payment_service: InitiateStkPushServiceProtocol,
    *,
    rate_limiter: RateLimiterProtocol | None = None,
    rate_limit_enabled: bool = False,
    rate_limit_window_seconds: int = 60,
    rate_limit_max_requests: int = 5,
    tool_policy: ToolPolicyEngine | None = None,
) -> McpToolResponse:
    """Validate MCP input and delegate generic payment initiation to the service."""

    policy_response = _check_tool_policy("initiate_payment", tool_policy)
    if policy_response is not None:
        return policy_response

    try:
        tool_input = _validate_initiate_payment_input(input_data)
    except ValidationError as exc:
        _log_tool_event("initiate_payment", "mcp_tool_invalid_input")
        return McpToolResponse(
            status="invalid_input",
            allowed=False,
            reason="Invalid initiate_payment input.",
            errors=[error["msg"] for error in exc.errors()],
        )

    rate_limit_response = _check_rate_limit(
        rate_limiter=rate_limiter,
        enabled=rate_limit_enabled,
        key=f"initiate_payment:{tool_input.phone_number}",
        limit=rate_limit_max_requests,
        window_seconds=rate_limit_window_seconds,
    )
    if rate_limit_response is not None:
        _log_tool_event("initiate_payment", "mcp_tool_rate_limited", status="rate_limited")
        return rate_limit_response

    _log_tool_event("initiate_payment", "mcp_tool_delegated")
    payment_response = payment_service.initiate_stk_push(
        phone_number=tool_input.phone_number,
        amount=tool_input.amount,
        account_reference=tool_input.account_reference,
        description=tool_input.description,
        idempotency_key=tool_input.idempotency_key,
        request_origin=MCP_AGENT_REQUEST_ORIGIN,
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


@with_correlation_context
def check_transaction_status_tool(
    input_data: CheckTransactionStatusInput | dict[str, Any],
    transaction_service: CheckTransactionStatusServiceProtocol,
    *,
    rate_limiter: RateLimiterProtocol | None = None,
    rate_limit_enabled: bool = False,
    rate_limit_window_seconds: int = 60,
    rate_limit_max_requests: int = 30,
    tool_policy: ToolPolicyEngine | None = None,
) -> McpToolResponse:
    """Validate MCP input and delegate transaction status checks to the service."""

    policy_response = _check_tool_policy("check_transaction_status", tool_policy)
    if policy_response is not None:
        return policy_response

    try:
        tool_input = _validate_transaction_status_input(input_data)
    except ValidationError as exc:
        _log_tool_event("check_transaction_status", "mcp_tool_invalid_input")
        return McpToolResponse(
            status="invalid_input",
            allowed=False,
            reason="Invalid check_transaction_status input.",
            errors=[error["msg"] for error in exc.errors()],
        )

    rate_limit_response = _check_rate_limit(
        rate_limiter=rate_limiter,
        enabled=rate_limit_enabled,
        key=f"check_transaction_status:{tool_input.checkout_request_id}",
        limit=rate_limit_max_requests,
        window_seconds=rate_limit_window_seconds,
    )
    if rate_limit_response is not None:
        _log_tool_event(
            "check_transaction_status",
            "mcp_tool_rate_limited",
            status="rate_limited",
        )
        return rate_limit_response

    _log_tool_event("check_transaction_status", "mcp_tool_delegated")
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


@with_correlation_context
def check_payment_status_tool(
    input_data: CheckPaymentStatusInput | dict[str, Any],
    transaction_service: CheckTransactionStatusServiceProtocol,
    *,
    rate_limiter: RateLimiterProtocol | None = None,
    rate_limit_enabled: bool = False,
    rate_limit_window_seconds: int = 60,
    rate_limit_max_requests: int = 30,
    tool_policy: ToolPolicyEngine | None = None,
) -> McpToolResponse:
    """Validate MCP input and delegate generic payment status checks to the service."""

    policy_response = _check_tool_policy("check_payment_status", tool_policy)
    if policy_response is not None:
        return policy_response

    try:
        tool_input = _validate_check_payment_status_input(input_data)
    except ValidationError as exc:
        _log_tool_event("check_payment_status", "mcp_tool_invalid_input")
        return McpToolResponse(
            status="invalid_input",
            allowed=False,
            reason="Invalid check_payment_status input.",
            errors=[error["msg"] for error in exc.errors()],
        )

    rate_limit_response = _check_rate_limit(
        rate_limiter=rate_limiter,
        enabled=rate_limit_enabled,
        key=f"check_payment_status:{tool_input.provider_transaction_id}",
        limit=rate_limit_max_requests,
        window_seconds=rate_limit_window_seconds,
    )
    if rate_limit_response is not None:
        _log_tool_event(
            "check_payment_status",
            "mcp_tool_rate_limited",
            status="rate_limited",
        )
        return rate_limit_response

    _log_tool_event("check_payment_status", "mcp_tool_delegated")
    transaction_response = transaction_service.check_transaction_status(
        tool_input.provider_transaction_id
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


@with_correlation_context
def generate_receipt_tool(
    input_data: GenerateReceiptInput | dict[str, Any],
    receipt_service: GenerateReceiptServiceProtocol,
    *,
    tool_policy: ToolPolicyEngine | None = None,
) -> McpToolResponse:
    """Validate MCP input and delegate receipt generation to the service."""

    policy_response = _check_tool_policy("generate_receipt", tool_policy)
    if policy_response is not None:
        return policy_response

    try:
        tool_input = _validate_generate_receipt_input(input_data)
    except ValidationError as exc:
        _log_tool_event("generate_receipt", "mcp_tool_invalid_input")
        return McpToolResponse(
            status="invalid_input",
            allowed=False,
            reason="Invalid generate_receipt input.",
            errors=[error["msg"] for error in exc.errors()],
        )

    _log_tool_event("generate_receipt", "mcp_tool_delegated")
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


@with_correlation_context
def get_today_summary_tool(
    input_data: GetTodaySummaryInput | dict[str, Any],
    analytics_service: AnalyticsServiceProtocol,
    *,
    tool_policy: ToolPolicyEngine | None = None,
) -> McpToolResponse:
    """Validate MCP input and delegate today's summary to the analytics service."""

    policy_response = _check_tool_policy("get_today_summary", tool_policy)
    if policy_response is not None:
        return policy_response

    try:
        _validate_today_summary_input(input_data)
    except ValidationError as exc:
        _log_tool_event("get_today_summary", "mcp_tool_invalid_input")
        return McpToolResponse(
            status="invalid_input",
            allowed=False,
            reason="Invalid get_today_summary input.",
            errors=[error["msg"] for error in exc.errors()],
        )

    _log_tool_event("get_today_summary", "mcp_tool_delegated")
    summary = analytics_service.get_today_summary()

    return McpToolResponse(
        status="ok",
        allowed=True,
        reason="Today's summary retrieved successfully.",
        data={"summary": summary.model_dump(mode="json")},
    )


@with_correlation_context
def get_failed_transactions_tool(
    input_data: GetFailedTransactionsInput | dict[str, Any],
    analytics_service: AnalyticsServiceProtocol,
    *,
    tool_policy: ToolPolicyEngine | None = None,
) -> McpToolResponse:
    """Validate MCP input and delegate failed transaction lookup to analytics service."""

    policy_response = _check_tool_policy("get_failed_transactions", tool_policy)
    if policy_response is not None:
        return policy_response

    try:
        _validate_failed_transactions_input(input_data)
    except ValidationError as exc:
        _log_tool_event("get_failed_transactions", "mcp_tool_invalid_input")
        return McpToolResponse(
            status="invalid_input",
            allowed=False,
            reason="Invalid get_failed_transactions input.",
            errors=[error["msg"] for error in exc.errors()],
        )

    _log_tool_event("get_failed_transactions", "mcp_tool_delegated")
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


@with_correlation_context
def run_reconciliation_tool(
    input_data: RunReconciliationInput | dict[str, Any],
    reconciliation_service: ReconciliationServiceProtocol,
    *,
    tool_policy: ToolPolicyEngine | None = None,
) -> McpToolResponse:
    """Validate MCP input and delegate reconciliation to the service."""

    policy_response = _check_tool_policy("run_reconciliation", tool_policy)
    if policy_response is not None:
        return policy_response

    try:
        _validate_run_reconciliation_input(input_data)
    except ValidationError as exc:
        _log_tool_event("run_reconciliation", "mcp_tool_invalid_input")
        return McpToolResponse(
            status="invalid_input",
            allowed=False,
            reason="Invalid run_reconciliation input.",
            errors=[error["msg"] for error in exc.errors()],
        )

    _log_tool_event("run_reconciliation", "mcp_tool_delegated")
    summary = reconciliation_service.run_reconciliation()
    return McpToolResponse(
        status=summary.status,
        allowed=True,
        reason="Reconciliation completed successfully.",
        data={"summary": summary.model_dump(mode="json")},
    )


def _validate_input(input_data: InitiateStkPushInput | dict[str, Any]) -> InitiateStkPushInput:
    if isinstance(input_data, InitiateStkPushInput):
        return input_data

    return InitiateStkPushInput.model_validate(input_data)


def _validate_initiate_payment_input(
    input_data: InitiatePaymentInput | dict[str, Any],
) -> InitiatePaymentInput:
    if isinstance(input_data, InitiatePaymentInput):
        return input_data

    return InitiatePaymentInput.model_validate(input_data)


def _validate_transaction_status_input(
    input_data: CheckTransactionStatusInput | dict[str, Any],
) -> CheckTransactionStatusInput:
    if isinstance(input_data, CheckTransactionStatusInput):
        return input_data

    return CheckTransactionStatusInput.model_validate(input_data)


def _validate_check_payment_status_input(
    input_data: CheckPaymentStatusInput | dict[str, Any],
) -> CheckPaymentStatusInput:
    if isinstance(input_data, CheckPaymentStatusInput):
        return input_data

    return CheckPaymentStatusInput.model_validate(input_data)


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


def _validate_run_reconciliation_input(
    input_data: RunReconciliationInput | dict[str, Any],
) -> RunReconciliationInput:
    if isinstance(input_data, RunReconciliationInput):
        return input_data

    return RunReconciliationInput.model_validate(input_data)


def _check_rate_limit(
    *,
    rate_limiter: RateLimiterProtocol | None,
    enabled: bool,
    key: str,
    limit: int,
    window_seconds: int,
) -> McpToolResponse | None:
    if not enabled or rate_limiter is None:
        return None

    try:
        decision = rate_limiter.allow(
            key=key,
            limit=limit,
            window_seconds=window_seconds,
        )
    except InfrastructureUnavailableError:
        logger.warning(
            "Rate limiter unavailable; sensitive tool call rejected.",
            extra={"event_type": "rate_limiter_unavailable", "status": "blocked"},
        )
        return McpToolResponse(
            status="infrastructure_unavailable",
            allowed=False,
            reason="Rate limiter unavailable; request rejected safely.",
        )
    except Exception:
        logger.exception(
            "Unexpected rate limiter failure; sensitive tool call rejected.",
            extra={"event_type": "rate_limiter_unavailable", "status": "blocked"},
        )
        return McpToolResponse(
            status="infrastructure_unavailable",
            allowed=False,
            reason="Rate limiter unavailable; request rejected safely.",
        )

    if decision.allowed:
        return None

    return McpToolResponse(
        status="rate_limited",
        allowed=False,
        reason="Rate limit exceeded",
        data={
            "key": decision.key,
            "limit": decision.limit,
            "reset_after_seconds": decision.reset_after_seconds,
        },
    )


def _check_tool_policy(
    tool_name: str,
    tool_policy: ToolPolicyEngine | None,
) -> McpToolResponse | None:
    if tool_policy is None:
        return None

    decision = tool_policy.evaluate(tool_name)
    if decision.allowed:
        return None

    _log_tool_event(
        tool_name,
        "mcp_tool_policy_denied",
        status=decision.status,
    )
    return McpToolResponse(
        status=decision.status,
        allowed=False,
        reason=decision.reason,
        requires_approval=decision.requires_approval,
    )


def _log_tool_event(tool_name: str, event_type: str, status: str | None = None) -> None:
    extra: dict[str, str] = {
        "event_type": event_type,
        "tool_name": tool_name,
    }
    if status is not None:
        extra["status"] = status
    logger.info("MCP tool event.", extra=extra)
