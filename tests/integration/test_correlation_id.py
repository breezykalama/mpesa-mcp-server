"""Integration tests for HTTP correlation ID tracing."""

from __future__ import annotations

from uuid import UUID

from app.bootstrap.container import AppContainer
from app.callbacks.routes import get_app_container
from app.config import Settings
from app.main import app
from app.observability.tracing import CORRELATION_ID_HEADER
from fastapi.testclient import TestClient


def test_middleware_injects_correlation_id() -> None:
    container = build_container()
    app.dependency_overrides[get_app_container] = lambda: container

    try:
        client = TestClient(app)
        response = client.get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    correlation_id = response.headers[CORRELATION_ID_HEADER]
    assert str(UUID(correlation_id)) == correlation_id


def test_middleware_preserves_provided_correlation_id() -> None:
    container = build_container()
    app.dependency_overrides[get_app_container] = lambda: container

    try:
        client = TestClient(app)
        response = client.get(
            "/health",
            headers={CORRELATION_ID_HEADER: "corr-client-123"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers[CORRELATION_ID_HEADER] == "corr-client-123"


def test_rejected_callback_audit_event_includes_correlation_id() -> None:
    container = build_container(callback_shared_secret="local-callback-secret")
    app.dependency_overrides[get_app_container] = lambda: container

    try:
        client = TestClient(app)
        response = client.post(
            "/callbacks/mpesa/stk",
            json={"Body": {"stkCallback": {}}},
            headers={CORRELATION_ID_HEADER: "corr-callback-123"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.headers[CORRELATION_ID_HEADER] == "corr-callback-123"
    assert container.audit_logger.events[-1].event_type == "stk_callback_rejected"
    assert container.audit_logger.events[-1].correlation_id == "corr-callback-123"


def build_container(callback_shared_secret: str | None = None) -> AppContainer:
    return AppContainer.mock(
        settings=Settings(
            database_url="postgresql+asyncpg://mpesa:mpesa@localhost:5432/mpesa_mcp",
            callback_shared_secret=callback_shared_secret,
        )
    )
