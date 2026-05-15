"""Callback routes."""

from __future__ import annotations

import logging
from secrets import compare_digest
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.bootstrap.container import AppContainer, create_app_container
from app.callbacks.handlers import CallbackProcessingResult, StkCallbackHandler

router = APIRouter()
logger = logging.getLogger(__name__)

_container = create_app_container()


def get_app_container() -> AppContainer:
    """Return the application container dependency."""

    return _container


def get_stk_callback_handler(
    container: Annotated[AppContainer, Depends(get_app_container)],
) -> StkCallbackHandler:
    """Return the STK callback handler dependency."""

    return StkCallbackHandler(
        transaction_repository=container.transaction_repository,
        audit_logger=container.audit_logger,
        metrics_recorder=container.metrics_recorder,
    )


def validate_callback_secret(
    container: Annotated[AppContainer, Depends(get_app_container)],
    callback_secret: Annotated[str | None, Header(alias="X-Callback-Secret")] = None,
) -> None:
    """Validate an optional shared secret for callback requests."""

    expected_secret = container.settings.callback_shared_secret
    if expected_secret is None or expected_secret == "":
        logger.info(
            "Callback accepted without shared secret.",
            extra={"event_type": "callback_accepted"},
        )
        return

    if callback_secret is not None and compare_digest(callback_secret, expected_secret):
        logger.info(
            "Callback shared secret validated.",
            extra={"event_type": "callback_accepted"},
        )
        return

    reason = (
        "Missing callback shared secret."
        if callback_secret is None
        else "Invalid callback shared secret."
    )
    container.audit_logger.log_event(
        "stk_callback_rejected",
        {"reason": reason},
        actor="mpesa_callback",
    )
    logger.warning(
        "Callback rejected.",
        extra={"event_type": "callback_rejected", "status": "unauthorized"},
    )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid callback credentials.",
    )


@router.post("/callbacks/mpesa/stk")
def handle_stk_callback(
    payload: dict[str, Any],
    _validated: Annotated[None, Depends(validate_callback_secret)],
    handler: Annotated[StkCallbackHandler, Depends(get_stk_callback_handler)],
) -> CallbackProcessingResult:
    """Handle an M-Pesa STK callback."""

    return handler.process(payload)
