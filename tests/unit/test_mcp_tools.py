"""Tests for MCP tool wrappers."""

from __future__ import annotations

from app.approvals.repository import InMemoryApprovalRepository
from app.approvals.service import ApprovalService
from app.audit.logger import InMemoryAuditLogger
from app.daraja.client import MockDarajaClient
from app.mcp.tools import initiate_stk_push_tool
from app.observability.metrics import InMemoryMetricsRecorder
from app.payments.providers import DarajaPaymentProvider
from app.policy.tool_policy import ToolPolicyEngine
from app.safety.policy import PaymentPolicy
from app.services.payment_service import PaymentResponse, PaymentService
from app.storage.repositories import InMemoryTransactionRepository


class RecordingPaymentService:
    """Payment service test double that records delegation."""

    def __init__(self) -> None:
        self.called = False
        self.phone_number: str | None = None
        self.amount: int | None = None
        self.account_reference: str | None = None
        self.description: str | None = None
        self.idempotency_key: str | None = None

    def initiate_stk_push(
        self,
        *,
        phone_number: str | None,
        amount: int | None,
        account_reference: str,
        description: str,
        idempotency_key: str | None = None,
    ) -> PaymentResponse:
        self.called = True
        self.phone_number = phone_number
        self.amount = amount
        self.account_reference = account_reference
        self.description = description
        self.idempotency_key = idempotency_key
        return PaymentResponse(
            status="pending",
            allowed=True,
            reason="Delegated.",
            transaction_id="txn_123",
        )


def build_payment_service(max_stk_amount: int = 10_000) -> PaymentService:
    return PaymentService(
        policy=PaymentPolicy(max_stk_amount=max_stk_amount),
        payment_provider=DarajaPaymentProvider(MockDarajaClient()),
        transaction_repository=InMemoryTransactionRepository(),
        audit_logger=InMemoryAuditLogger(),
        approval_service=ApprovalService(approval_repository=InMemoryApprovalRepository()),
        metrics_recorder=InMemoryMetricsRecorder(),
    )


def test_successful_stk_push_response() -> None:
    response = initiate_stk_push_tool(
        {
            "phone_number": "254700000000",
            "amount": 1_000,
            "account_reference": "INV-001",
            "description": "Invoice payment",
        },
        build_payment_service(),
    )

    assert response.status == "pending"
    assert response.allowed is True
    assert response.data["transaction_id"] is not None
    assert response.data["checkout_request_id"] is not None


def test_approval_required_response() -> None:
    response = initiate_stk_push_tool(
        {
            "phone_number": "254700000000",
            "amount": 10_001,
            "account_reference": "INV-002",
            "description": "Invoice payment",
        },
        build_payment_service(max_stk_amount=10_000),
    )

    assert response.status == "approval_required"
    assert response.allowed is False
    assert response.requires_approval is True
    assert response.data["approval_id"] is not None


def test_invalid_input_handled_cleanly() -> None:
    service = RecordingPaymentService()

    response = initiate_stk_push_tool(
        {
            "amount": 1_000,
            "account_reference": "INV-003",
            "description": "Invoice payment",
        },
        service,
    )

    assert response.status == "invalid_input"
    assert response.allowed is False
    assert response.errors != []
    assert service.called is False


def test_tool_delegates_to_payment_service() -> None:
    service = RecordingPaymentService()

    response = initiate_stk_push_tool(
        {
            "phone_number": "254700000000",
            "amount": 1_000,
            "account_reference": "INV-004",
            "description": "Invoice payment",
            "idempotency_key": "mcp-key",
        },
        service,
    )

    assert service.called is True
    assert service.phone_number == "254700000000"
    assert service.amount == 1_000
    assert service.account_reference == "INV-004"
    assert service.description == "Invoice payment"
    assert service.idempotency_key == "mcp-key"
    assert response.status == "pending"


def test_blocked_tool_denied_by_policy() -> None:
    service = RecordingPaymentService()
    policy = ToolPolicyEngine(blocked_tools={"initiate_stk_push"})

    response = initiate_stk_push_tool(
        {
            "phone_number": "254700000000",
            "amount": 1_000,
            "account_reference": "INV-005",
            "description": "Invoice payment",
        },
        service,
        tool_policy=policy,
    )

    assert response.status == "blocked"
    assert response.allowed is False
    assert response.reason == "Tool disabled by policy"
    assert service.called is False


def test_approval_required_tool_returns_policy_approval_required() -> None:
    service = RecordingPaymentService()
    policy = ToolPolicyEngine(approval_required_tools={"initiate_stk_push"})

    response = initiate_stk_push_tool(
        {
            "phone_number": "254700000000",
            "amount": 1_000,
            "account_reference": "INV-006",
            "description": "Invoice payment",
        },
        service,
        tool_policy=policy,
    )

    assert response.status == "approval_required"
    assert response.allowed is False
    assert response.requires_approval is True
    assert response.reason == "Tool requires approval by policy"
    assert service.called is False
