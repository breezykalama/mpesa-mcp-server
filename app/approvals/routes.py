"""Operator-facing approval routes."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.approvals.models import ApprovalRequest
from app.approvals.service import ApprovalService, ApprovalServiceResponse
from app.bootstrap.container import AppContainer
from app.callbacks.routes import get_app_container
from app.services.payment_service import ApprovalExecutionResponse, PaymentService

router = APIRouter(prefix="/approvals", tags=["approvals"])
logger = logging.getLogger(__name__)


def get_approval_service(
    container: Annotated[AppContainer, Depends(get_app_container)],
) -> ApprovalService:
    """Return the approval service dependency."""

    return container.approval_service


def get_payment_service(
    container: Annotated[AppContainer, Depends(get_app_container)],
) -> PaymentService:
    """Return the payment service dependency."""

    return container.payment_service


@router.get("/pending", response_model=None)
def list_pending_approvals(
    approval_service: Annotated[ApprovalService, Depends(get_approval_service)],
) -> dict[str, list[ApprovalRequest]]:
    """Return all pending approval requests."""

    logger.info(
        "Pending approvals requested.",
        extra={"event_type": "approval_pending_list_requested"},
    )
    return {"approvals": approval_service.list_pending_requests()}


@router.get("/{approval_id}", response_model=None)
def get_approval(
    approval_id: str,
    approval_service: Annotated[ApprovalService, Depends(get_approval_service)],
) -> ApprovalRequest | JSONResponse:
    """Return approval request details."""

    approval = approval_service.get_approval_request(approval_id)
    if approval is None:
        logger.info(
            "Approval request not found.",
            extra={
                "event_type": "approval_lookup_failed",
                "approval_id": approval_id,
                "status": "not_found",
            },
        )
        return _not_found_response()

    logger.info(
        "Approval request retrieved.",
        extra={
            "event_type": "approval_lookup_succeeded",
            "approval_id": approval_id,
            "status": approval.status,
        },
    )
    return approval


@router.post("/{approval_id}/approve", response_model=None)
def approve_payment_request(
    approval_id: str,
    payment_service: Annotated[PaymentService, Depends(get_payment_service)],
) -> ApprovalExecutionResponse | JSONResponse:
    """Approve and execute an approval request."""

    response = payment_service.execute_approved_payment(approval_id)
    logger.info(
        "Approval approve endpoint completed.",
        extra={
            "event_type": "approval_route_approved",
            "approval_id": approval_id,
            "status": response.status,
        },
    )
    if response.status == "not_found":
        return _response_with_status(response, status.HTTP_404_NOT_FOUND)

    return response


@router.post("/{approval_id}/reject", response_model=None)
def reject_payment_request(
    approval_id: str,
    approval_service: Annotated[ApprovalService, Depends(get_approval_service)],
) -> ApprovalServiceResponse | JSONResponse:
    """Reject an approval request."""

    response = approval_service.reject_request(approval_id)
    logger.info(
        "Approval reject endpoint completed.",
        extra={
            "event_type": "approval_route_rejected",
            "approval_id": approval_id,
            "status": response.status,
        },
    )
    if response.status == "not_found":
        return _response_with_status(response, status.HTTP_404_NOT_FOUND)

    return response


def _not_found_response() -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={
            "status": "not_found",
            "allowed": False,
            "reason": "Approval request was not found.",
        },
    )


def _response_with_status(
    response: ApprovalExecutionResponse | ApprovalServiceResponse,
    status_code: int,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(response),
    )
