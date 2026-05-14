"""Tests for the payment service vertical slice."""

from __future__ import annotations

from app.approvals.repository import InMemoryApprovalRepository
from app.approvals.service import ApprovalService
from app.audit.logger import InMemoryAuditLogger
from app.daraja.client import (
    DarajaClientProtocol,
    MockDarajaClient,
    StkPushResponse,
    TransactionStatusResponse,
)
from app.observability.metrics import InMemoryMetricsRecorder
from app.safety.policy import PaymentPolicy
from app.services.payment_service import PaymentService
from app.storage.repositories import InMemoryTransactionRepository


class FailingDarajaClient:
    """Daraja client test double that raises an error."""

    def initiate_stk_push(
        self,
        phone_number: str,
        amount: int,
        account_reference: str,
        description: str,
    ) -> StkPushResponse:
        raise RuntimeError("network unavailable")

    def check_transaction_status(self, checkout_request_id: str) -> TransactionStatusResponse:
        raise RuntimeError("network unavailable")


class CountingDarajaClient:
    """Daraja client test double that counts STK push calls."""

    def __init__(self) -> None:
        self.stk_push_calls = 0

    def initiate_stk_push(
        self,
        phone_number: str,
        amount: int,
        account_reference: str,
        description: str,
    ) -> StkPushResponse:
        self.stk_push_calls += 1
        return StkPushResponse(
            checkout_request_id=f"ws_CO_{self.stk_push_calls}",
            merchant_request_id=f"merchant_{self.stk_push_calls}",
            response_code="0",
            response_description="Success. Request accepted for processing.",
        )

    def check_transaction_status(self, checkout_request_id: str) -> TransactionStatusResponse:
        return TransactionStatusResponse(
            checkout_request_id=checkout_request_id,
            result_code="0",
            result_description="Success",
            status="completed",
        )


def build_service(
    *,
    max_stk_amount: int = 10_000,
    daraja_client: DarajaClientProtocol | None = None,
) -> tuple[PaymentService, InMemoryTransactionRepository, InMemoryAuditLogger]:
    service, repository, audit_logger, _approval_service = build_service_components(
        max_stk_amount=max_stk_amount,
        daraja_client=daraja_client,
    )
    return service, repository, audit_logger


def build_service_components(
    *,
    max_stk_amount: int = 10_000,
    daraja_client: DarajaClientProtocol | None = None,
) -> tuple[
    PaymentService,
    InMemoryTransactionRepository,
    InMemoryAuditLogger,
    ApprovalService,
]:
    repository = InMemoryTransactionRepository()
    audit_logger = InMemoryAuditLogger()
    approval_service = ApprovalService(approval_repository=InMemoryApprovalRepository())
    service = PaymentService(
        policy=PaymentPolicy(max_stk_amount=max_stk_amount),
        daraja_client=daraja_client or MockDarajaClient(),
        transaction_repository=repository,
        audit_logger=audit_logger,
        approval_service=approval_service,
        metrics_recorder=InMemoryMetricsRecorder(),
    )
    return service, repository, audit_logger, approval_service


def test_successful_mocked_stk_push() -> None:
    service, repository, _audit_logger = build_service()

    response = service.initiate_stk_push(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-001",
        description="Invoice payment",
    )

    assert response.status == "pending"
    assert response.allowed is True
    assert response.transaction_id is not None
    assert response.checkout_request_id is not None
    assert response.merchant_request_id is not None

    transaction = repository.get_transaction(response.transaction_id)
    assert transaction is not None
    assert transaction.amount == 1_000
    assert transaction.phone_number == "254700000000"


def test_amount_above_limit_returns_approval_required() -> None:
    service, repository, audit_logger = build_service(max_stk_amount=500)

    response = service.initiate_stk_push(
        phone_number="254700000000",
        amount=501,
        account_reference="INV-002",
        description="Invoice payment",
    )

    assert response.status == "approval_required"
    assert response.allowed is False
    assert response.requires_approval is True
    assert response.approval_id is not None
    assert response.transaction_id is None
    assert repository.get_transaction("missing") is None
    assert audit_logger.events == []


def test_missing_phone_number_blocked() -> None:
    service, _repository, audit_logger = build_service()

    response = service.initiate_stk_push(
        phone_number=None,
        amount=1_000,
        account_reference="INV-003",
        description="Invoice payment",
    )

    assert response.status == "blocked"
    assert response.allowed is False
    assert "Phone number is required" in response.reason
    assert audit_logger.events == []


def test_daraja_failure_returns_failed_response() -> None:
    service, _repository, audit_logger = build_service(daraja_client=FailingDarajaClient())

    response = service.initiate_stk_push(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-004",
        description="Invoice payment",
    )

    assert response.status == "failed"
    assert response.allowed is False
    assert "Daraja STK push failed" in response.reason
    assert audit_logger.events == []


def test_audit_log_is_written_on_success() -> None:
    service, _repository, audit_logger = build_service()

    response = service.initiate_stk_push(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-005",
        description="Invoice payment",
    )

    assert len(audit_logger.events) == 1
    event = audit_logger.events[0]
    assert event.event_type == "stk_push_initiated"
    assert event.payload["transaction_id"] == response.transaction_id
    assert event.payload["amount"] == 1_000


def test_duplicate_request_does_not_call_daraja_twice() -> None:
    daraja_client = CountingDarajaClient()
    service, _repository, _audit_logger = build_service(daraja_client=daraja_client)

    first_response = service.initiate_stk_push(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-006",
        description="Invoice payment",
        idempotency_key="same-key",
    )
    second_response = service.initiate_stk_push(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-006",
        description="Invoice payment",
        idempotency_key="same-key",
    )

    assert daraja_client.stk_push_calls == 1
    assert second_response.transaction_id == first_response.transaction_id
    assert second_response.checkout_request_id == first_response.checkout_request_id
    assert second_response.reason == "Existing transaction returned for idempotency key."


def test_same_idempotency_key_returns_existing_transaction() -> None:
    daraja_client = CountingDarajaClient()
    service, _repository, _audit_logger = build_service(daraja_client=daraja_client)

    first_response = service.initiate_stk_push(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-007",
        description="Invoice payment",
        idempotency_key="fixed-key",
    )
    second_response = service.initiate_stk_push(
        phone_number="254711111111",
        amount=9_000,
        account_reference="DIFFERENT",
        description="Different payment",
        idempotency_key="fixed-key",
    )

    assert daraja_client.stk_push_calls == 1
    assert second_response.transaction_id == first_response.transaction_id
    assert second_response.idempotency_key == "fixed-key"


def test_different_idempotency_key_creates_new_transaction() -> None:
    daraja_client = CountingDarajaClient()
    service, _repository, _audit_logger = build_service(daraja_client=daraja_client)

    first_response = service.initiate_stk_push(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-008",
        description="Invoice payment",
        idempotency_key="key-one",
    )
    second_response = service.initiate_stk_push(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-008",
        description="Invoice payment",
        idempotency_key="key-two",
    )

    assert daraja_client.stk_push_calls == 2
    assert second_response.transaction_id != first_response.transaction_id
    assert second_response.idempotency_key == "key-two"


def test_missing_idempotency_key_uses_deterministic_key() -> None:
    daraja_client = CountingDarajaClient()
    service, _repository, _audit_logger = build_service(daraja_client=daraja_client)

    first_response = service.initiate_stk_push(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-009",
        description="Invoice payment",
    )
    second_response = service.initiate_stk_push(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-009",
        description="Invoice payment",
    )

    assert daraja_client.stk_push_calls == 1
    assert first_response.idempotency_key is not None
    assert second_response.idempotency_key == first_response.idempotency_key


def test_repository_finds_transaction_by_idempotency_key() -> None:
    _service, repository, _audit_logger = build_service()
    transaction = repository.save_pending_transaction(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-010",
        description="Invoice payment",
        checkout_request_id="ws_CO_010",
        merchant_request_id="merchant_010",
        idempotency_key="lookup-key",
    )

    found = repository.find_by_idempotency_key("lookup-key")

    assert found is not None
    assert found.transaction_id == transaction.transaction_id


def test_above_limit_stk_creates_approval_request() -> None:
    repository = InMemoryTransactionRepository()
    audit_logger = InMemoryAuditLogger()
    approval_service = ApprovalService(approval_repository=InMemoryApprovalRepository())
    service = PaymentService(
        policy=PaymentPolicy(max_stk_amount=500),
        daraja_client=MockDarajaClient(),
        transaction_repository=repository,
        audit_logger=audit_logger,
        approval_service=approval_service,
        metrics_recorder=InMemoryMetricsRecorder(),
    )

    response = service.initiate_stk_push(
        phone_number="254700000000",
        amount=501,
        account_reference="INV-011",
        description="Invoice payment",
    )

    assert response.status == "approval_required"
    assert response.requires_approval is True
    assert response.approval_id is not None
    approval = approval_service.get_approval_request(response.approval_id)
    assert approval is not None
    assert approval.status == "pending"
    assert approval.action == "initiate_stk_push"
    assert approval.payload["amount"] == 501


def test_daraja_not_called_when_approval_required() -> None:
    daraja_client = CountingDarajaClient()
    service, repository, _audit_logger = build_service(
        max_stk_amount=500,
        daraja_client=daraja_client,
    )

    response = service.initiate_stk_push(
        phone_number="254700000000",
        amount=501,
        account_reference="INV-012",
        description="Invoice payment",
    )

    assert response.status == "approval_required"
    assert daraja_client.stk_push_calls == 0
    assert repository.list_transactions() == []


def test_approving_pending_request_executes_stk_push() -> None:
    daraja_client = CountingDarajaClient()
    service, repository, _audit_logger, _approval_service = build_service_components(
        max_stk_amount=500,
        daraja_client=daraja_client,
    )
    approval_response = service.initiate_stk_push(
        phone_number="254700000000",
        amount=501,
        account_reference="INV-013",
        description="Invoice payment",
        idempotency_key="approval-key",
    )

    assert approval_response.approval_id is not None
    execution_response = service.execute_approved_payment(approval_response.approval_id)

    assert execution_response.status == "approved"
    assert execution_response.payment is not None
    assert execution_response.payment.status == "pending"
    assert execution_response.payment.idempotency_key == "approval-key"
    assert daraja_client.stk_push_calls == 1
    assert len(repository.list_transactions()) == 1


def test_approval_execution_uses_original_payload() -> None:
    daraja_client = CountingDarajaClient()
    service, repository, _audit_logger, _approval_service = build_service_components(
        max_stk_amount=500,
        daraja_client=daraja_client,
    )
    approval_response = service.initiate_stk_push(
        phone_number="254722222222",
        amount=999,
        account_reference="INV-014",
        description="Original description",
        idempotency_key="original-key",
    )

    assert approval_response.approval_id is not None
    execution_response = service.execute_approved_payment(approval_response.approval_id)

    assert execution_response.payment is not None
    assert execution_response.payment.status == "pending"
    transaction = repository.find_by_idempotency_key("original-key")
    assert transaction is not None
    assert transaction.phone_number == "254722222222"
    assert transaction.amount == 999
    assert transaction.account_reference == "INV-014"
    assert transaction.description == "Original description"


def test_duplicate_approval_does_not_execute_twice() -> None:
    daraja_client = CountingDarajaClient()
    service, _repository, _audit_logger, _approval_service = build_service_components(
        max_stk_amount=500,
        daraja_client=daraja_client,
    )
    approval_response = service.initiate_stk_push(
        phone_number="254700000000",
        amount=501,
        account_reference="INV-015",
        description="Invoice payment",
        idempotency_key="duplicate-approval-key",
    )

    assert approval_response.approval_id is not None
    first_execution = service.execute_approved_payment(approval_response.approval_id)
    second_execution = service.execute_approved_payment(approval_response.approval_id)

    assert first_execution.payment is not None
    assert first_execution.payment.status == "pending"
    assert second_execution.status == "blocked"
    assert second_execution.payment is None
    assert daraja_client.stk_push_calls == 1


def test_rejected_request_cannot_execute() -> None:
    daraja_client = CountingDarajaClient()
    service, _repository, _audit_logger, approval_service = build_service_components(
        max_stk_amount=500,
        daraja_client=daraja_client,
    )
    approval_response = service.initiate_stk_push(
        phone_number="254700000000",
        amount=501,
        account_reference="INV-016",
        description="Invoice payment",
    )

    assert approval_response.approval_id is not None
    approval_service.reject_request(approval_response.approval_id)
    execution_response = service.execute_approved_payment(approval_response.approval_id)

    assert execution_response.status == "blocked"
    assert execution_response.payment is None
    assert daraja_client.stk_push_calls == 0


def test_missing_approval_id_returns_not_found() -> None:
    service, _repository, _audit_logger, _approval_service = build_service_components()

    execution_response = service.execute_approved_payment("missing")

    assert execution_response.status == "not_found"
    assert execution_response.allowed is False
    assert execution_response.payment is None


def test_daraja_failure_after_approval_is_handled_cleanly() -> None:
    service, _repository, _audit_logger, _approval_service = build_service_components(
        max_stk_amount=500,
        daraja_client=FailingDarajaClient(),
    )
    approval_response = service.initiate_stk_push(
        phone_number="254700000000",
        amount=501,
        account_reference="INV-017",
        description="Invoice payment",
    )

    assert approval_response.approval_id is not None
    execution_response = service.execute_approved_payment(approval_response.approval_id)

    assert execution_response.status == "approved"
    assert execution_response.allowed is False
    assert execution_response.approval is not None
    assert execution_response.approval.status == "approved"
    assert execution_response.payment is not None
    assert execution_response.payment.status == "failed"


def test_idempotency_prevents_duplicate_execution_for_approved_payload() -> None:
    daraja_client = CountingDarajaClient()
    service, repository, _audit_logger, approval_service = build_service_components(
        max_stk_amount=500,
        daraja_client=daraja_client,
    )
    first_approval = service.initiate_stk_push(
        phone_number="254700000000",
        amount=501,
        account_reference="INV-018",
        description="Invoice payment",
        idempotency_key="shared-approved-key",
    )
    second_approval = approval_service.create_approval_request(
        action="initiate_stk_push",
        payload={
            "phone_number": "254700000000",
            "amount": 501,
            "account_reference": "INV-018",
            "description": "Invoice payment",
            "idempotency_key": "shared-approved-key",
        },
        reason="Manual duplicate approval.",
    )

    assert first_approval.approval_id is not None
    first_execution = service.execute_approved_payment(first_approval.approval_id)
    second_execution = service.execute_approved_payment(second_approval.approval_id)

    assert first_execution.payment is not None
    assert second_execution.payment is not None
    assert second_execution.payment.reason == "Existing transaction returned for idempotency key."
    assert daraja_client.stk_push_calls == 1
    assert len(repository.list_transactions()) == 1
