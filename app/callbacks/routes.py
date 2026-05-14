"""Callback routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.bootstrap.container import AppContainer, create_app_container
from app.callbacks.handlers import CallbackProcessingResult, StkCallbackHandler

router = APIRouter()

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


@router.post("/callbacks/mpesa/stk")
def handle_stk_callback(
    payload: dict[str, Any],
    handler: Annotated[StkCallbackHandler, Depends(get_stk_callback_handler)],
) -> CallbackProcessingResult:
    """Handle an M-Pesa STK callback."""

    return handler.process(payload)
