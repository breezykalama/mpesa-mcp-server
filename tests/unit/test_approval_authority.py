"""Tests for human approval authority, approval state integrity, and audit attribution."""

from __future__ import annotations

import pytest
from app.approvals.repository import InMemoryApprovalRepository
from app.approvals.service import (
    REVIEWER_NOT_AUTHORIZED_STATUS,
    ApprovalService,
    ApprovalServiceResponse,
)
from app.audit.logger import InMemoryAuditLogger
from app.audit.repository import AuditEvent
from app.auth.oidc import OperatorIdentity
from app.bootstrap.container import AppContainer
from app.config import Settings, allows_development_approval_authority
from app.daraja.client import MockDarajaClient, StkPushResponse
from app.observability.metrics import InMemoryMetricsRecorder
from app.payments.providers import DarajaPaymentProvider
from app.safety.policy import PaymentPolicy
from app.services.payment_service import ApprovalExecutionResponse, PaymentService
from app.storage.repositories import InMemoryTransactionRepository
from tests.identity_fixtures import (
    application_principal,
    development_identity,
    static_token_identity,
    verified_human,
)

TEST_DATABASE_URL = "postgresql+asyncpg://mpesa:mpesa@localhost:5432/mpesa_mcp"


class CountingDarajaClient(MockDarajaClient):
    """Mock Daraja client that counts STK push provider calls."""

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
        return super().initiate_stk_push(phone_number, amount, account_reference, description)


def build(
    *,
    allow_development_reviewers: bool = False,
    expiry_minutes: int = 30,
) -> tuple[ApprovalService, PaymentService, InMemoryAuditLogger, CountingDarajaClient]:
    audit_logger = InMemoryAuditLogger()
    approval_service = ApprovalService(
        approval_repository=InMemoryApprovalRepository(),
        audit_logger=audit_logger,
        expiry_minutes=expiry_minutes,
        high_risk_amount_threshold=50_000,
        high_risk_required_reviewers=2,
        allow_development_reviewers=allow_development_reviewers,
    )
    daraja_client = CountingDarajaClient()
    payment_service = PaymentService(
        policy=PaymentPolicy(max_stk_amount=500),
        payment_provider=DarajaPaymentProvider(daraja_client),
        transaction_repository=InMemoryTransactionRepository(),
        audit_logger=audit_logger,
        approval_service=approval_service,
        metrics_recorder=InMemoryMetricsRecorder(),
    )
    return approval_service, payment_service, audit_logger, daraja_client


def request_payment(payment_service: PaymentService, *, amount: int = 1_000) -> str:
    response = payment_service.initiate_stk_push(
        phone_number="254700000000",
        amount=amount,
        account_reference=f"INV-AUTH-{amount}",
        description="Approval authority test payment",
        request_origin="mcp_agent",
    )
    assert response.approval_id is not None
    return response.approval_id


def events_of(audit_logger: InMemoryAuditLogger, event_type: str) -> list[AuditEvent]:
    return [event for event in audit_logger.events if event.event_type == event_type]


# --- Reviewer eligibility ---------------------------------------------------------


@pytest.mark.parametrize(
    "reviewer",
    [
        static_token_identity(),
        development_identity(),
        verified_human(roles=["viewer", "approver"]).model_copy(
            update={"principal_type": "unknown"}
        ),
        application_principal(),
    ],
    ids=["static-token", "development-oidc", "jwks-without-delegation", "app-only"],
)
def test_unverified_reviewers_cannot_approve_outside_development(
    reviewer: OperatorIdentity,
) -> None:
    approval_service, payment_service, _audit, daraja = build()
    approval_id = request_payment(payment_service)

    response = payment_service.execute_approved_payment(approval_id, reviewer=reviewer)

    approval = approval_service.get_approval_request(approval_id)
    assert response.status == REVIEWER_NOT_AUTHORIZED_STATUS
    assert response.allowed is False
    assert response.payment is None
    assert approval is not None
    assert approval.status == "pending"
    assert approval.reviewer_ids == []
    assert daraja.stk_push_calls == 0


@pytest.mark.parametrize(
    "reviewer",
    [static_token_identity(), development_identity(), application_principal()],
    ids=["static-token", "development-oidc", "app-only"],
)
def test_unverified_reviewers_cannot_reject_outside_development(
    reviewer: OperatorIdentity,
) -> None:
    approval_service, payment_service, _audit, _daraja = build()
    approval_id = request_payment(payment_service)

    response = approval_service.reject_request(approval_id, reviewer=reviewer)

    approval = approval_service.get_approval_request(approval_id)
    assert response.status == REVIEWER_NOT_AUTHORIZED_STATUS
    assert approval is not None
    assert approval.status == "pending"


def test_application_identity_is_denied_even_when_development_authority_is_allowed() -> None:
    approval_service, payment_service, _audit, daraja = build(allow_development_reviewers=True)
    approval_id = request_payment(payment_service)

    approve = payment_service.execute_approved_payment(
        approval_id,
        reviewer=application_principal(),
    )
    reject = approval_service.reject_request(approval_id, reviewer=application_principal())

    assert approve.status == REVIEWER_NOT_AUTHORIZED_STATUS
    assert reject.status == REVIEWER_NOT_AUTHORIZED_STATUS
    assert daraja.stk_push_calls == 0


def test_viewer_role_cannot_decide_at_service_layer() -> None:
    approval_service, payment_service, _audit, daraja = build()
    approval_id = request_payment(payment_service)
    viewer = verified_human("verified-viewer", roles=["viewer"])

    approve = payment_service.execute_approved_payment(approval_id, reviewer=viewer)
    reject = approval_service.reject_request(approval_id, reviewer=viewer)

    assert approve.status == REVIEWER_NOT_AUTHORIZED_STATUS
    assert reject.status == REVIEWER_NOT_AUTHORIZED_STATUS
    assert daraja.stk_push_calls == 0


def test_verified_human_can_approve_and_payment_executes_once() -> None:
    _approval_service, payment_service, _audit, daraja = build()
    approval_id = request_payment(payment_service)

    response = payment_service.execute_approved_payment(
        approval_id,
        reviewer=verified_human(),
    )

    assert response.status == "approved"
    assert response.payment is not None
    assert response.payment.status == "pending"
    assert daraja.stk_push_calls == 1


def test_development_identity_may_approve_single_reviewer_request_in_development() -> None:
    _approval_service, payment_service, _audit, daraja = build(
        allow_development_reviewers=True
    )
    approval_id = request_payment(payment_service)

    response = payment_service.execute_approved_payment(
        approval_id,
        reviewer=static_token_identity(),
    )

    assert response.status == "approved"
    assert daraja.stk_push_calls == 1


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (static_token_identity("operator-approver"), static_token_identity("operator-admin")),
        (development_identity("dev-1"), development_identity("dev-2")),
        (verified_human("human-1"), static_token_identity("operator-admin")),
    ],
    ids=["two-static-tokens", "two-development-identities", "human-plus-static-token"],
)
def test_synthetic_identities_cannot_satisfy_multi_reviewer_requirement(
    first: OperatorIdentity,
    second: OperatorIdentity,
) -> None:
    approval_service, payment_service, _audit, daraja = build(
        allow_development_reviewers=True
    )
    approval_id = request_payment(payment_service, amount=50_000)

    first_response = payment_service.execute_approved_payment(approval_id, reviewer=first)
    second_response = payment_service.execute_approved_payment(approval_id, reviewer=second)

    approval = approval_service.get_approval_request(approval_id)
    assert approval is not None
    assert approval.status == "pending"
    assert second_response.status == REVIEWER_NOT_AUTHORIZED_STATUS
    assert first_response.status in {"partially_approved", REVIEWER_NOT_AUTHORIZED_STATUS}
    assert daraja.stk_push_calls == 0


def test_two_distinct_verified_humans_satisfy_multi_reviewer_requirement() -> None:
    approval_service, payment_service, _audit, daraja = build()
    approval_id = request_payment(payment_service, amount=50_000)

    first = payment_service.execute_approved_payment(
        approval_id,
        reviewer=verified_human("human-1"),
    )
    second = payment_service.execute_approved_payment(
        approval_id,
        reviewer=verified_human("human-2"),
    )

    assert first.status == "partially_approved"
    assert second.status == "approved"
    approval = approval_service.get_approval_request(approval_id)
    assert approval is not None
    assert approval.reviewer_ids == ["human-1", "human-2"]
    assert daraja.stk_push_calls == 1


def test_same_verified_human_cannot_count_twice() -> None:
    _approval_service, payment_service, _audit, daraja = build()
    approval_id = request_payment(payment_service, amount=50_000)

    payment_service.execute_approved_payment(approval_id, reviewer=verified_human("human-1"))
    duplicate = payment_service.execute_approved_payment(
        approval_id,
        reviewer=verified_human("human-1"),
    )

    assert duplicate.status == "blocked"
    assert daraja.stk_push_calls == 0


# --- Approval state integrity -----------------------------------------------------


def test_rejection_only_from_pending_after_approval() -> None:
    approval_service, payment_service, _audit, daraja = build()
    approval_id = request_payment(payment_service)
    payment_service.execute_approved_payment(approval_id, reviewer=verified_human("human-1"))

    response = approval_service.reject_request(approval_id, reviewer=verified_human("human-2"))

    approval = approval_service.get_approval_request(approval_id)
    assert response.status == "blocked"
    assert response.reason == "Approval request is not pending."
    assert approval is not None
    assert approval.status == "approved"
    assert daraja.stk_push_calls == 1


def test_duplicate_rejection_is_deterministic() -> None:
    approval_service, payment_service, _audit, _daraja = build()
    approval_id = request_payment(payment_service)

    first = approval_service.reject_request(approval_id, reviewer=verified_human("human-1"))
    second = approval_service.reject_request(approval_id, reviewer=verified_human("human-1"))
    third = approval_service.reject_request(approval_id, reviewer=verified_human("human-2"))

    approval = approval_service.get_approval_request(approval_id)
    assert first.status == "rejected"
    assert second.status == "blocked"
    assert third.status == "blocked"
    assert approval is not None
    assert approval.status == "rejected"


def test_expired_approval_cannot_be_rejected_into_rejected_state() -> None:
    approval_service, payment_service, _audit, _daraja = build(expiry_minutes=0)
    approval_id = request_payment(payment_service)

    first = approval_service.reject_request(approval_id, reviewer=verified_human())
    second = approval_service.reject_request(approval_id, reviewer=verified_human())

    approval = approval_service.get_approval_request(approval_id)
    assert first.status == "expired"
    assert second.status == "blocked"
    assert approval is not None
    assert approval.status == "expired"


def test_rejected_and_expired_approvals_cannot_execute_payment() -> None:
    approval_service, payment_service, _audit, daraja = build()
    rejected_id = request_payment(payment_service, amount=1_001)
    approval_service.reject_request(rejected_id, reviewer=verified_human())

    _expiring_service, expiring_payments, _audit2, expiring_daraja = build(expiry_minutes=0)
    expired_id = request_payment(expiring_payments, amount=1_002)

    rejected = payment_service.execute_approved_payment(rejected_id, reviewer=verified_human())
    expired = expiring_payments.execute_approved_payment(expired_id, reviewer=verified_human())

    assert rejected.status == "blocked"
    assert expired.status == "expired"
    assert daraja.stk_push_calls == 0
    assert expiring_daraja.stk_push_calls == 0


def test_repeated_approval_decisions_do_not_create_additional_payment_actions() -> None:
    approval_service, payment_service, _audit, daraja = build()
    approval_id = request_payment(payment_service)

    responses = [
        payment_service.execute_approved_payment(approval_id, reviewer=verified_human(f"h-{i}"))
        for i in range(3)
    ]

    assert [response.status for response in responses] == ["approved", "blocked", "blocked"]
    approval = approval_service.get_approval_request(approval_id)
    assert approval is not None
    assert approval.reviewer_ids == ["h-0"]
    assert daraja.stk_push_calls == 1


def test_execute_approved_payment_has_no_default_reviewer() -> None:
    _approval_service, payment_service, _audit, _daraja = build()

    with pytest.raises(TypeError):
        payment_service.execute_approved_payment("approval-id")  # type: ignore[call-arg]


# --- Audit attribution ------------------------------------------------------------


def test_approval_audit_records_authenticated_human_identity() -> None:
    _approval_service, payment_service, audit_logger, _daraja = build()
    approval_id = request_payment(payment_service)
    reviewer = verified_human(
        "human-approver",
        email="human.approver@example.test",
        display_name="Human Approver",
    )

    payment_service.execute_approved_payment(approval_id, reviewer=reviewer)

    [approved_event] = events_of(audit_logger, "approval_approved")
    assert approved_event.actor == "authenticated_operator"
    assert approved_event.operator_subject == "human-approver"
    assert approved_event.operator_email == "human.approver@example.test"
    assert approved_event.operator_display_name == "Human Approver"
    assert approved_event.operator_roles == ["viewer", "approver"]
    assert approved_event.payload["decision_authority"] == "human_operator"
    assert approved_event.payload["reviewer_assurance"] == "verified_human"
    assert approved_event.payload["authentication_method"] == "oidc_jwks"
    assert approved_event.payload["reviewer_ids"] == ["human-approver"]


def test_partial_approval_and_rejection_audit_record_identity() -> None:
    approval_service, payment_service, audit_logger, _daraja = build()
    high_risk_id = request_payment(payment_service, amount=50_000)
    rejected_id = request_payment(payment_service, amount=1_000)

    payment_service.execute_approved_payment(high_risk_id, reviewer=verified_human("human-1"))
    approval_service.reject_request(rejected_id, reviewer=verified_human("human-2"))

    [partial] = events_of(audit_logger, "approval_partially_approved")
    [rejected] = events_of(audit_logger, "approval_rejected")
    assert partial.operator_subject == "human-1"
    assert rejected.operator_subject == "human-2"
    assert rejected.actor == "authenticated_operator"
    assert rejected.operator_roles == ["viewer", "approver"]


def test_denied_attempt_is_audited_with_actual_identity_and_without_credentials() -> None:
    _approval_service, payment_service, audit_logger, _daraja = build()
    approval_id = request_payment(payment_service)

    payment_service.execute_approved_payment(approval_id, reviewer=static_token_identity())

    [denied] = events_of(audit_logger, "approval_decision_denied")
    assert denied.operator_subject == "operator-approver"
    assert denied.payload["authentication_method"] == "static_token"
    assert denied.payload["reviewer_assurance"] == "unverified"
    assert denied.payload["decision"] == "approve"
    serialized = denied.model_dump_json()
    assert "token" not in serialized.replace("static_token", "")
    assert "Bearer" not in serialized


def test_request_creation_is_attributed_to_requester_not_a_human() -> None:
    _approval_service, payment_service, audit_logger, _daraja = build()

    request_payment(payment_service)

    [created] = events_of(audit_logger, "approval_created")
    assert created.actor == "mcp_agent"
    assert created.payload["request_origin"] == "mcp_agent"
    assert created.operator_subject is None


def test_expiry_is_attributed_to_system_without_fabricated_identity() -> None:
    approval_service, payment_service, audit_logger, _daraja = build(expiry_minutes=0)
    request_payment(payment_service)

    approval_service.expire_stale_approvals()

    [expired] = events_of(audit_logger, "approval_expired")
    assert expired.actor == "system"
    assert expired.operator_subject is None


# --- Environment gating -----------------------------------------------------------


@pytest.mark.parametrize(
    ("app_env", "daraja_mode", "expected"),
    [
        ("development", "mock", True),
        ("test", "sandbox", True),
        ("production", "mock", False),
        ("staging", "sandbox", False),
        ("development", "production", False),
    ],
)
def test_development_approval_authority_is_environment_gated(
    app_env: str,
    daraja_mode: str,
    expected: bool,
) -> None:
    settings = Settings(
        database_url=TEST_DATABASE_URL,
        app_env=app_env,
        daraja_mode=daraja_mode,
        daraja_production_consumer_key="placeholder",
        daraja_production_consumer_secret="placeholder",
        daraja_production_passkey="placeholder",
        daraja_production_shortcode="placeholder",
        daraja_production_callback_url="https://callbacks.example.test/stk",
    )

    assert allows_development_approval_authority(settings) is expected


def test_container_denies_static_token_reviewers_outside_development() -> None:
    container = AppContainer.mock(
        settings=Settings(
            database_url=TEST_DATABASE_URL,
            app_env="staging",
            operator_approver_token="approver-token",
        )
    )
    response = container.payment_service.initiate_stk_push(
        phone_number="254700000000",
        amount=container.settings.max_stk_amount + 1,
        account_reference="INV-STAGING-001",
        description="Staging approval test",
    )
    assert response.approval_id is not None

    result = container.payment_service.execute_approved_payment(
        response.approval_id,
        reviewer=static_token_identity(),
    )

    assert result.status == REVIEWER_NOT_AUTHORIZED_STATUS
    assert container.transaction_repository.list_transactions() == []



# --- Authorization ordering (F2) --------------------------------------------------

INELIGIBLE_PRINCIPALS = {
    "app-only": application_principal(),
    "verified-viewer": verified_human("verified-viewer", roles=["viewer"]),
    "static-token": static_token_identity(),
    "development-oidc": development_identity(),
    "jwks-without-delegation": verified_human("no-scp").model_copy(
        update={"principal_type": "unknown"}
    ),
}
REQUEST_STATES = ("pending", "expired_unmutated", "approved", "rejected", "expired", "missing")


def build_request_in_state(
    state: str,
) -> tuple[ApprovalService, PaymentService, InMemoryAuditLogger, CountingDarajaClient, str]:
    """Return services plus an approval ID whose stored request is in ``state``."""

    approval_service, payment_service, audit_logger, daraja = build(
        expiry_minutes=0 if state.startswith("expired") else 30
    )
    if state == "missing":
        return approval_service, payment_service, audit_logger, daraja, "missing-approval"

    approval_id = request_payment(payment_service)
    if state == "approved":
        payment_service.execute_approved_payment(approval_id, reviewer=verified_human("h-1"))
    elif state == "rejected":
        approval_service.reject_request(approval_id, reviewer=verified_human("h-1"))
    elif state == "expired":
        approval_service.expire_stale_approvals()
    return approval_service, payment_service, audit_logger, daraja, approval_id


@pytest.mark.parametrize("state", REQUEST_STATES)
@pytest.mark.parametrize("principal_name", list(INELIGIBLE_PRINCIPALS))
@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_ineligible_principal_is_denied_before_any_state_handling(
    decision: str,
    principal_name: str,
    state: str,
) -> None:
    approval_service, payment_service, audit_logger, daraja, approval_id = (
        build_request_in_state(state)
    )
    stored_before = approval_service.get_approval_request(approval_id)
    provider_calls_before = daraja.stk_push_calls
    events_before = len(audit_logger.events)
    principal = INELIGIBLE_PRINCIPALS[principal_name]

    response: ApprovalExecutionResponse | ApprovalServiceResponse
    if decision == "approve":
        response = payment_service.execute_approved_payment(approval_id, reviewer=principal)
        assert response.payment is None
    else:
        response = approval_service.reject_request(approval_id, reviewer=principal)

    assert response.status == REVIEWER_NOT_AUTHORIZED_STATUS
    assert response.allowed is False
    assert response.approval is None
    assert approval_service.get_approval_request(approval_id) == stored_before
    assert daraja.stk_push_calls == provider_calls_before
    new_events = audit_logger.events[events_before:]
    assert [event.event_type for event in new_events] == ["approval_decision_denied"]
    assert new_events[0].operator_subject == principal.subject
    assert new_events[0].payload["decision"] == decision
    assert new_events[0].payload["approval_id"] == approval_id


def test_ineligible_principal_does_not_expire_stale_requests() -> None:
    approval_service, payment_service, audit_logger, _daraja = build(expiry_minutes=0)
    target_id = request_payment(payment_service, amount=1_001)
    other_id = request_payment(payment_service, amount=1_002)

    payment_service.execute_approved_payment(target_id, reviewer=application_principal())
    approval_service.reject_request(target_id, reviewer=application_principal())

    for approval_id in (target_id, other_id):
        approval = approval_service.get_approval_request(approval_id)
        assert approval is not None
        assert approval.status == "pending"
    assert events_of(audit_logger, "approval_expired") == []


def test_quorum_denial_happens_before_expiry_or_state_changes() -> None:
    approval_service, payment_service, audit_logger, daraja = build(
        allow_development_reviewers=True,
        expiry_minutes=0,
    )
    approval_id = request_payment(payment_service, amount=50_000)

    response = payment_service.execute_approved_payment(
        approval_id,
        reviewer=static_token_identity(),
    )

    approval = approval_service.get_approval_request(approval_id)
    assert response.status == REVIEWER_NOT_AUTHORIZED_STATUS
    assert response.approval is None
    assert approval is not None
    assert approval.status == "pending"
    assert approval.reviewer_ids == []
    assert events_of(audit_logger, "approval_expired") == []
    assert daraja.stk_push_calls == 0


def test_denied_audit_truncates_caller_supplied_approval_id() -> None:
    approval_service, _payment_service, audit_logger, _daraja = build()

    approval_service.reject_request("x" * 500, reviewer=application_principal())

    [denied] = events_of(audit_logger, "approval_decision_denied")
    assert denied.payload["approval_id"] == "x" * 64


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_eligible_reviewer_still_sees_expiry_after_authorization(decision: str) -> None:
    approval_service, payment_service, _audit, daraja = build(expiry_minutes=0)
    approval_id = request_payment(payment_service)

    response: ApprovalExecutionResponse | ApprovalServiceResponse
    if decision == "approve":
        response = payment_service.execute_approved_payment(
            approval_id,
            reviewer=verified_human(),
        )
    else:
        response = approval_service.reject_request(approval_id, reviewer=verified_human())

    assert response.status == "expired"
    approval = approval_service.get_approval_request(approval_id)
    assert approval is not None
    assert approval.status == "expired"
    assert daraja.stk_push_calls == 0


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_eligible_reviewer_on_missing_request_gets_not_found(decision: str) -> None:
    approval_service, payment_service, _audit, _daraja = build()

    response: ApprovalExecutionResponse | ApprovalServiceResponse
    if decision == "approve":
        response = payment_service.execute_approved_payment("missing", reviewer=verified_human())
    else:
        response = approval_service.reject_request("missing", reviewer=verified_human())

    assert response.status == "not_found"


# --- Development exception scope (Codex P2) ---------------------------------------
def auth_disabled_identity() -> OperatorIdentity:
    return OperatorIdentity(
        subject="local-admin",
        display_name="Local Administrator",
        roles=["admin"],
        authentication_method="auth_disabled",
    )



@pytest.mark.parametrize("decision", ["approve", "reject"])
@pytest.mark.parametrize(
    "reviewer",
    [
        verified_human("jwks-unclassified").model_copy(update={"principal_type": "unknown"}),
        application_principal(),
    ],
    ids=["jwks-unknown", "jwks-application"],
)
def test_jwks_principals_never_receive_development_exception(
    reviewer: OperatorIdentity,
    decision: str,
) -> None:
    approval_service, payment_service, audit_logger, daraja = build(
        allow_development_reviewers=True
    )
    approval_id = request_payment(payment_service)
    stored_before = approval_service.get_approval_request(approval_id)

    response: ApprovalExecutionResponse | ApprovalServiceResponse
    if decision == "approve":
        response = payment_service.execute_approved_payment(approval_id, reviewer=reviewer)
    else:
        response = approval_service.reject_request(approval_id, reviewer=reviewer)

    assert response.status == REVIEWER_NOT_AUTHORIZED_STATUS
    assert approval_service.get_approval_request(approval_id) == stored_before
    assert daraja.stk_push_calls == 0
    [denied] = events_of(audit_logger, "approval_decision_denied")
    assert denied.payload["authentication_method"] == "oidc_jwks"


@pytest.mark.parametrize(
    "reviewer",
    [static_token_identity(), development_identity(), auth_disabled_identity()],
    ids=["static-token", "development-oidc", "auth-disabled"],
)
def test_only_development_methods_receive_development_exception(
    reviewer: OperatorIdentity,
) -> None:
    _approval_service, payment_service, _audit, daraja = build(allow_development_reviewers=True)
    approval_id = request_payment(payment_service)

    response = payment_service.execute_approved_payment(approval_id, reviewer=reviewer)

    assert response.status == "approved"
    assert daraja.stk_push_calls == 1
