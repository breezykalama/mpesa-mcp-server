"""Operator dashboard API routes."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.analytics.service import AnalyticsService
from app.audit.repository import AuditRepositoryProtocol
from app.auth.security import OperatorPrincipal, require_admin, require_viewer
from app.bootstrap.container import AppContainer
from app.callbacks.routes import get_app_container
from app.reconciliation.service import ReconciliationService
from app.storage.repositories import PendingTransaction, TransactionRepositoryProtocol

router = APIRouter(prefix="/operator", tags=["operator"])
logger = logging.getLogger(__name__)


class OperatorTransactionSummary(BaseModel):
    """Transaction fields shown in operator transaction lists."""

    transaction_id: str
    provider: str
    rail: str
    status: str
    amount: int
    phone_number: str
    created_at: str


class OperatorAuditEventSummary(BaseModel):
    """Audit event fields shown in operator audit lists."""

    event_id: str
    event_type: str
    created_at: str
    actor: str | None = None
    correlation_id: str | None = None


def get_transaction_repository(
    container: Annotated[AppContainer, Depends(get_app_container)],
) -> TransactionRepositoryProtocol:
    """Return the transaction repository dependency."""

    return container.transaction_repository


def get_audit_repository(
    container: Annotated[AppContainer, Depends(get_app_container)],
) -> AuditRepositoryProtocol:
    """Return the audit repository dependency."""

    return container.audit_repository


def get_analytics_service(
    container: Annotated[AppContainer, Depends(get_app_container)],
) -> AnalyticsService:
    """Return the analytics service dependency."""

    return container.analytics_service


def get_reconciliation_service(
    container: Annotated[AppContainer, Depends(get_app_container)],
) -> ReconciliationService:
    """Return the reconciliation service dependency."""

    return container.reconciliation_service


@router.get("/transactions")
def list_transactions(
    transaction_repository: Annotated[
        TransactionRepositoryProtocol,
        Depends(get_transaction_repository),
    ],
    _principal: Annotated[OperatorPrincipal, Depends(require_viewer)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, list[OperatorTransactionSummary]]:
    """List recent transactions for operator visibility."""

    transactions = transaction_repository.list_recent_transactions(limit=limit)
    logger.info(
        "Operator transactions listed.",
        extra={"event_type": "operator_transactions_listed", "count": len(transactions)},
    )
    return {
        "transactions": [
            OperatorTransactionSummary(
                transaction_id=transaction.transaction_id,
                provider=transaction.provider,
                rail=transaction.rail,
                status=transaction.status,
                amount=transaction.amount,
                phone_number=transaction.phone_number,
                created_at=transaction.created_at.isoformat(),
            )
            for transaction in transactions
        ]
    }


@router.get("/transactions/{transaction_id}", response_model=None)
def get_transaction(
    transaction_id: str,
    transaction_repository: Annotated[
        TransactionRepositoryProtocol,
        Depends(get_transaction_repository),
    ],
    _principal: Annotated[OperatorPrincipal, Depends(require_viewer)],
) -> PendingTransaction | JSONResponse:
    """Return transaction details."""

    transaction = transaction_repository.get_transaction(transaction_id)
    if transaction is None:
        logger.info(
            "Operator transaction lookup failed.",
            extra={
                "event_type": "operator_transaction_not_found",
                "transaction_id": transaction_id,
                "status": "not_found",
            },
        )
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "status": "not_found",
                "reason": "Transaction was not found.",
            },
        )

    logger.info(
        "Operator transaction retrieved.",
        extra={
            "event_type": "operator_transaction_retrieved",
            "transaction_id": transaction_id,
            "status": transaction.status,
        },
    )
    return transaction


@router.get("/audit-events")
def list_audit_events(
    audit_repository: Annotated[AuditRepositoryProtocol, Depends(get_audit_repository)],
    _principal: Annotated[OperatorPrincipal, Depends(require_viewer)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, list[OperatorAuditEventSummary]]:
    """List recent audit events for operator visibility."""

    events = audit_repository.list_recent_events(limit=limit)
    logger.info(
        "Operator audit events listed.",
        extra={"event_type": "operator_audit_events_listed", "count": len(events)},
    )
    return {
        "audit_events": [
            OperatorAuditEventSummary(
                event_id=event.event_id,
                event_type=event.event_type,
                created_at=event.created_at.isoformat(),
                actor=event.actor,
                correlation_id=event.correlation_id,
            )
            for event in events
        ]
    }


@router.get("/analytics/today")
def get_today_analytics(
    analytics_service: Annotated[AnalyticsService, Depends(get_analytics_service)],
    _principal: Annotated[OperatorPrincipal, Depends(require_viewer)],
) -> dict[str, object]:
    """Return today's analytics summary."""

    summary = analytics_service.get_today_summary()
    logger.info(
        "Operator analytics summary retrieved.",
        extra={"event_type": "operator_analytics_today_retrieved"},
    )
    return {"summary": summary.model_dump(mode="json")}


@router.post("/reconciliation/run")
def run_reconciliation(
    reconciliation_service: Annotated[
        ReconciliationService,
        Depends(get_reconciliation_service),
    ],
    _principal: Annotated[OperatorPrincipal, Depends(require_admin)],
) -> dict[str, object]:
    """Run a read-only reconciliation pass."""

    summary = reconciliation_service.run_reconciliation()
    logger.info(
        "Operator reconciliation run completed.",
        extra={
            "event_type": "operator_reconciliation_completed",
            "finding_count": summary.finding_count,
        },
    )
    return {"summary": summary.model_dump(mode="json")}
