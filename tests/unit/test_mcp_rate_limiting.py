"""Tests for MCP tool rate limiting."""

from __future__ import annotations

from app.infrastructure.health import InfrastructureUnavailableError
from app.mcp.tools import (
    check_transaction_status_tool,
    initiate_stk_push_tool,
)
from app.rate_limit.limiter import InMemoryRateLimiter, RateLimitDecision
from app.services.payment_service import PaymentResponse
from app.services.transaction_service import TransactionStatusServiceResponse


class FailingRateLimiter:
    """Rate limiter that simulates infrastructure failure."""

    def allow(
        self,
        *,
        key: str,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision:
        raise InfrastructureUnavailableError("Redis unavailable.")


class CountingPaymentService:
    """Payment service test double that counts calls."""

    def __init__(self) -> None:
        self.calls = 0

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
        self.calls += 1
        return PaymentResponse(
            status="pending",
            allowed=True,
            reason="STK push initiated successfully.",
            transaction_id=f"txn-{self.calls}",
        )


class CountingTransactionService:
    """Transaction service test double that counts calls."""

    def __init__(self) -> None:
        self.calls = 0

    def check_transaction_status(
        self,
        checkout_request_id: str,
    ) -> TransactionStatusServiceResponse:
        self.calls += 1
        return TransactionStatusServiceResponse(
            status="completed",
            allowed=True,
            reason="Status checked.",
            checkout_request_id=checkout_request_id,
        )


def test_stk_push_is_allowed_within_limit() -> None:
    service = CountingPaymentService()
    limiter = InMemoryRateLimiter()

    response = initiate_stk_push_tool(
        stk_payload(),
        service,
        rate_limiter=limiter,
        rate_limit_enabled=True,
        rate_limit_max_requests=1,
    )

    assert response.status == "pending"


def test_rate_limiter_unavailable_rejects_payment_safely() -> None:
    service = CountingPaymentService()

    response = initiate_stk_push_tool(
        stk_payload(),
        service,
        rate_limiter=FailingRateLimiter(),
        rate_limit_enabled=True,
        rate_limit_max_requests=1,
    )

    assert response.status == "infrastructure_unavailable"
    assert response.allowed is False
    assert service.calls == 0


def test_stk_push_is_blocked_after_limit() -> None:
    service = CountingPaymentService()
    limiter = InMemoryRateLimiter()

    initiate_stk_push_tool(
        stk_payload(),
        service,
        rate_limiter=limiter,
        rate_limit_enabled=True,
        rate_limit_max_requests=1,
    )
    response = initiate_stk_push_tool(
        stk_payload(),
        service,
        rate_limiter=limiter,
        rate_limit_enabled=True,
        rate_limit_max_requests=1,
    )

    assert response.status == "rate_limited"
    assert response.allowed is False
    assert response.reason == "Rate limit exceeded"
    assert service.calls == 1


def test_status_check_is_blocked_after_limit() -> None:
    service = CountingTransactionService()
    limiter = InMemoryRateLimiter()

    check_transaction_status_tool(
        {"checkout_request_id": "ws_CO_123"},
        service,
        rate_limiter=limiter,
        rate_limit_enabled=True,
        rate_limit_max_requests=1,
    )
    response = check_transaction_status_tool(
        {"checkout_request_id": "ws_CO_123"},
        service,
        rate_limiter=limiter,
        rate_limit_enabled=True,
        rate_limit_max_requests=1,
    )

    assert response.status == "rate_limited"
    assert response.allowed is False
    assert service.calls == 1


def test_rate_limiting_can_be_disabled() -> None:
    service = CountingPaymentService()
    limiter = InMemoryRateLimiter()

    first = initiate_stk_push_tool(
        stk_payload(),
        service,
        rate_limiter=limiter,
        rate_limit_enabled=False,
        rate_limit_max_requests=1,
    )
    second = initiate_stk_push_tool(
        stk_payload(),
        service,
        rate_limiter=limiter,
        rate_limit_enabled=False,
        rate_limit_max_requests=1,
    )

    assert first.status == "pending"
    assert second.status == "pending"
    assert service.calls == 2


def test_mcp_tool_returns_rate_limited_cleanly() -> None:
    service = CountingPaymentService()
    limiter = InMemoryRateLimiter()

    initiate_stk_push_tool(
        stk_payload(),
        service,
        rate_limiter=limiter,
        rate_limit_enabled=True,
        rate_limit_max_requests=1,
    )
    response = initiate_stk_push_tool(
        stk_payload(),
        service,
        rate_limiter=limiter,
        rate_limit_enabled=True,
        rate_limit_max_requests=1,
    )

    assert response.model_dump(exclude={"data"}) == {
        "status": "rate_limited",
        "allowed": False,
        "reason": "Rate limit exceeded",
        "requires_approval": False,
        "errors": [],
    }
    assert response.data["limit"] == 1
    assert service.calls == 1


def stk_payload() -> dict[str, object]:
    return {
        "phone_number": "254700000000",
        "amount": 1_000,
        "account_reference": "INV-001",
        "description": "Invoice payment",
    }
