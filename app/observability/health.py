"""Health and metrics routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.bootstrap.container import AppContainer
from app.callbacks.routes import get_app_container
from app.config_validation import (
    StartupConfigValidationError,
    validate_startup_settings,
)
from app.infrastructure.health import readiness_dependency_results
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

    service_dependencies_ready = all(
        dependency is not None
        for dependency in (
            container.daraja_client,
            container.payment_provider,
            container.transaction_repository,
            container.audit_logger,
            container.payment_service,
            container.transaction_service,
            container.receipt_service,
            container.analytics_service,
            container.reconciliation_service,
        )
    )
    config_valid = True
    config_reason = "valid"
    try:
        validate_startup_settings(container.settings)
    except StartupConfigValidationError as exc:
        config_valid = False
        config_reason = str(exc)

    dependency_results = readiness_dependency_results(container.settings)
    dependencies_ready = all(result.ok for result in dependency_results)
    ready = service_dependencies_ready and config_valid and dependencies_ready

    return {
        "status": "ready" if ready else "not_ready",
        "ready": ready,
        "storage_mode": container.settings.storage_mode,
        "provider_mode": container.settings.daraja_mode,
        "payment_provider": container.settings.payment_provider,
        "config": {"ok": config_valid, "reason": config_reason},
        "dependencies": {
            result.name: {"ok": result.ok, "reason": result.reason}
            for result in dependency_results
        },
        "services": {"ok": service_dependencies_ready},
    }


@router.get("/metrics")
def metrics(container: Annotated[AppContainer, Depends(get_app_container)]) -> MetricsSnapshot:
    """Return current in-memory metrics."""

    return container.metrics_recorder.snapshot()
