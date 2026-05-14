"""Health and metrics routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.bootstrap.container import AppContainer
from app.callbacks.routes import get_app_container
from app.observability.metrics import MetricsSnapshot

router = APIRouter()


@router.get("/health")
def health(container: Annotated[AppContainer, Depends(get_app_container)]) -> dict[str, str]:
    """Return liveness status."""

    return {
        "status": "ok",
        "storage_mode": container.settings.storage_mode,
    }


@router.get("/health/ready")
def readiness(container: Annotated[AppContainer, Depends(get_app_container)]) -> dict[str, object]:
    """Return readiness status."""

    dependencies_ready = all(
        dependency is not None
        for dependency in (
            container.daraja_client,
            container.transaction_repository,
            container.audit_logger,
            container.payment_service,
            container.transaction_service,
            container.receipt_service,
            container.analytics_service,
        )
    )

    return {
        "status": "ready" if dependencies_ready else "not_ready",
        "ready": dependencies_ready,
        "storage_mode": container.settings.storage_mode,
    }


@router.get("/metrics")
def metrics(container: Annotated[AppContainer, Depends(get_app_container)]) -> MetricsSnapshot:
    """Return current in-memory metrics."""

    return container.metrics_recorder.snapshot()
