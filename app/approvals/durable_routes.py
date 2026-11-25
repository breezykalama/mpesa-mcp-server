"""Operator routes for isolated, non-executing durable approval decisions (Slice D4.2).

These routes live under ``/durable-approvals`` and are separate from the live
``/approvals`` routes, which are unchanged. They:

* take the reviewer only from the server-side ``require_approver`` dependency; the
  request body accepts an optional ``comment`` and nothing else (unknown fields such as
  a reviewer, issuer, roles or principal type are rejected with 422);
* record decisions through ``DurableApprovalDecisionService`` (D3 locked transaction,
  verified-human / trusted-issuer / distinct-reviewer rules, derived quorum);
* never call ``PaymentService``, ``ApprovalService`` or any payment provider. Reaching
  quorum returns ``payment_execution: "not_performed"``.

When ``STORAGE_MODE`` is not ``postgres`` there is no durable store and every route
returns 503 (fail closed; there is no in-memory fallback).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.approvals.durable_decisions import DurableApprovalDecisionService
from app.approvals.durable_store import DurableApprovalStore
from app.approvals.postgres_repository import (
    ApprovalDecisionRejectedError,
    ApprovalExpiredError,
    ApprovalIntegrityError,
    ApprovalNotPendingError,
    ApprovalPersistenceError,
    ApprovalRequestNotFoundError,
    DecisionOutcome,
    DuplicateReviewerDecisionError,
)
from app.approvals.reviewer import ReviewerNotEligibleError
from app.auth.security import OperatorPrincipal, require_approver
from app.bootstrap.container import AppContainer
from app.callbacks.routes import get_app_container
from app.observability.tracing import get_correlation_id

router = APIRouter(prefix="/durable-approvals", tags=["durable-approvals"])
logger = logging.getLogger(__name__)

MAX_DECISION_COMMENT_LENGTH = 1000
PAYMENT_EXECUTION_NOT_PERFORMED = "not_performed"


class DurableDecisionRequest(BaseModel):
    """Optional decision body. Reviewer identity is never accepted from the request."""

    model_config = ConfigDict(extra="forbid")

    comment: str | None = Field(default=None, max_length=MAX_DECISION_COMMENT_LENGTH)


def get_durable_approval_store(
    container: Annotated[AppContainer, Depends(get_app_container)],
) -> DurableApprovalStore | None:
    """Return the durable approval store, or ``None`` when storage is not PostgreSQL."""

    return container.durable_approval_store


def get_durable_approval_decisions(
    container: Annotated[AppContainer, Depends(get_app_container)],
) -> DurableApprovalDecisionService | None:
    """Return the durable decision service, or ``None`` when storage is not PostgreSQL."""

    return container.durable_approval_decisions


@router.get("/{approval_id}", response_model=None)
def get_durable_approval(
    approval_id: str,
    store: Annotated[DurableApprovalStore | None, Depends(get_durable_approval_store)],
    _principal: Annotated[OperatorPrincipal, Depends(require_approver)],
) -> JSONResponse:
    """Return a durable approval request with its decisions."""

    if store is None:
        return _unavailable()
    try:
        record = store.get_approval_request(approval_id)
    except ApprovalIntegrityError:
        return _integrity_failure(approval_id)
    if record is None:
        return _error(status.HTTP_404_NOT_FOUND, "not_found", "Approval request was not found.")
    return JSONResponse(content=jsonable_encoder({"approval": record}))


@router.post("/{approval_id}/approve", response_model=None)
def approve_durable_approval(
    approval_id: str,
    decisions: Annotated[
        DurableApprovalDecisionService | None, Depends(get_durable_approval_decisions)
    ],
    principal: Annotated[OperatorPrincipal, Depends(require_approver)],
    body: DurableDecisionRequest | None = None,
) -> JSONResponse:
    """Record the authenticated operator's approval. Never executes a payment."""

    return _decide(approval_id, "approve", decisions, principal, body)


@router.post("/{approval_id}/reject", response_model=None)
def reject_durable_approval(
    approval_id: str,
    decisions: Annotated[
        DurableApprovalDecisionService | None, Depends(get_durable_approval_decisions)
    ],
    principal: Annotated[OperatorPrincipal, Depends(require_approver)],
    body: DurableDecisionRequest | None = None,
) -> JSONResponse:
    """Record the authenticated operator's rejection. Never calls a payment provider."""

    return _decide(approval_id, "reject", decisions, principal, body)


def _decide(
    approval_id: str,
    decision: Literal["approve", "reject"],
    decisions: DurableApprovalDecisionService | None,
    principal: OperatorPrincipal,
    body: DurableDecisionRequest | None,
) -> JSONResponse:
    if decisions is None:
        return _unavailable()

    action = decisions.approve if decision == "approve" else decisions.reject
    comment = body.comment if body is not None else None
    try:
        outcome = action(
            approval_id,
            reviewer=principal,
            comment=comment,
            correlation_id=get_correlation_id(),
        )
    except ReviewerNotEligibleError as exc:
        _log_refused(approval_id, decision, principal, "reviewer_not_authorized")
        return _error(status.HTTP_403_FORBIDDEN, "reviewer_not_authorized", str(exc))
    except ApprovalRequestNotFoundError:
        return _error(status.HTTP_404_NOT_FOUND, "not_found", "Approval request was not found.")
    except ApprovalIntegrityError:
        return _integrity_failure(approval_id)
    except ApprovalDecisionRejectedError as exc:
        reason_code = _rejection_code(exc)
        _log_refused(approval_id, decision, principal, reason_code)
        return _error(status.HTTP_409_CONFLICT, reason_code, str(exc))
    except ApprovalPersistenceError as exc:
        _log_refused(approval_id, decision, principal, "decision_conflict")
        return _error(status.HTTP_409_CONFLICT, "decision_conflict", str(exc))

    logger.info(
        "Durable approval decision recorded.",
        extra={
            "event_type": "durable_approval_decision_recorded",
            "approval_id": approval_id,
            "decision": decision,
            "status": outcome.status,
            "operator_id": principal.operator_id,
        },
    )
    return JSONResponse(content=jsonable_encoder(_outcome_body(outcome)))


def _outcome_body(outcome: DecisionOutcome) -> dict[str, Any]:
    return {
        "status": outcome.status,
        "approval": outcome.record,
        "decision": outcome.decision,
        "payment_execution": PAYMENT_EXECUTION_NOT_PERFORMED,
    }


def _rejection_code(exc: ApprovalDecisionRejectedError) -> str:
    if isinstance(exc, DuplicateReviewerDecisionError):
        return "duplicate_decision"
    if isinstance(exc, ApprovalExpiredError):
        return "expired"
    if isinstance(exc, ApprovalNotPendingError):
        return "not_pending"
    return "decision_refused"


def _log_refused(
    approval_id: str,
    decision: str,
    principal: OperatorPrincipal,
    reason: str,
) -> None:
    logger.warning(
        "Durable approval decision refused.",
        extra={
            "event_type": "durable_approval_decision_refused",
            "approval_id": approval_id,
            "decision": decision,
            "status": reason,
            "operator_id": principal.operator_id,
        },
    )


def _integrity_failure(approval_id: str) -> JSONResponse:
    logger.error(
        "Durable approval integrity check failed.",
        extra={
            "event_type": "durable_approval_integrity_failed",
            "approval_id": approval_id,
        },
    )
    return _error(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "integrity_check_failed",
        "Stored approval request failed its integrity check.",
    )


def _unavailable() -> JSONResponse:
    return _error(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "durable_approvals_unavailable",
        "Durable approvals require STORAGE_MODE=postgres.",
    )


def _error(status_code: int, code: str, reason: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"status": code, "allowed": False, "reason": reason},
    )
