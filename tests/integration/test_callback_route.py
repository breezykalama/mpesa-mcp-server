"""Integration tests for callback routes."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from app.audit.logger import InMemoryAuditLogger
from app.bootstrap.container import AppContainer
from app.callbacks.handlers import StkCallbackHandler
from app.callbacks.routes import get_app_container, get_stk_callback_handler
from app.config import Settings
from app.main import app
from app.observability.metrics import InMemoryMetricsRecorder
from app.storage.repositories import InMemoryTransactionRepository
from fastapi.testclient import TestClient


def test_fastapi_callback_route_returns_expected_response() -> None:
    repository = InMemoryTransactionRepository()
    audit_logger = InMemoryAuditLogger()
    repository.save_pending_transaction(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-001",
        description="Invoice payment",
        checkout_request_id="ws_CO_ROUTE",
        merchant_request_id="mock_123",
    )

    def override_handler() -> StkCallbackHandler:
        return StkCallbackHandler(
            transaction_repository=repository,
            audit_logger=audit_logger,
            metrics_recorder=InMemoryMetricsRecorder(),
        )

    app.dependency_overrides[get_stk_callback_handler] = override_handler

    try:
        client = TestClient(app)
        response = client.post("/callbacks/mpesa/stk", json=stk_callback_payload())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["success"] is True
    assert body["checkout_request_id"] == "ws_CO_ROUTE"

    transaction = repository.find_by_checkout_request_id("ws_CO_ROUTE")
    assert transaction is not None
    assert transaction.status == "completed"
    assert transaction.mpesa_receipt_number == "RCPROUTE"


def test_callback_accepted_when_no_secret_configured() -> None:
    container = build_callback_container(callback_shared_secret=None)
    seed_route_transaction(container)

    app.dependency_overrides[get_app_container] = lambda: container

    try:
        client = TestClient(app)
        response = client.post("/callbacks/mpesa/stk", json=stk_callback_payload())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_callback_accepted_with_valid_secret() -> None:
    container = build_callback_container(callback_shared_secret="local-callback-secret")
    seed_route_transaction(container)

    app.dependency_overrides[get_app_container] = lambda: container

    try:
        client = TestClient(app)
        response = client.post(
            "/callbacks/mpesa/stk",
            json=stk_callback_payload(),
            headers={"X-Callback-Secret": "local-callback-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_callback_rejected_with_missing_secret() -> None:
    container = build_callback_container(callback_shared_secret="local-callback-secret")

    app.dependency_overrides[get_app_container] = lambda: container

    try:
        client = TestClient(app)
        response = client.post("/callbacks/mpesa/stk", json=stk_callback_payload())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid callback credentials."


def test_callback_rejected_with_wrong_secret() -> None:
    container = build_callback_container(callback_shared_secret="local-callback-secret")

    app.dependency_overrides[get_app_container] = lambda: container

    try:
        client = TestClient(app)
        response = client.post(
            "/callbacks/mpesa/stk",
            json=stk_callback_payload(),
            headers={"X-Callback-Secret": "wrong-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid callback credentials."


def test_rejected_callback_writes_audit_event() -> None:
    container = build_callback_container(callback_shared_secret="local-callback-secret")

    app.dependency_overrides[get_app_container] = lambda: container

    try:
        client = TestClient(app)
        response = client.post("/callbacks/mpesa/stk", json=stk_callback_payload())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert container.audit_logger.events[-1].event_type == "stk_callback_rejected"
    assert container.audit_logger.events[-1].payload == {
        "reason": "Missing callback shared secret."
    }


def test_duplicate_callback_is_rejected() -> None:
    container = build_callback_container(callback_shared_secret=None)
    seed_route_transaction(container)

    app.dependency_overrides[get_app_container] = lambda: container

    try:
        client = TestClient(app)
        first_response = client.post("/callbacks/mpesa/stk", json=stk_callback_payload())
        duplicate_response = client.post("/callbacks/mpesa/stk", json=stk_callback_payload())
    finally:
        app.dependency_overrides.clear()

    assert first_response.status_code == 200
    assert duplicate_response.status_code == 409
    assert duplicate_response.json() == {
        "status": "duplicate_callback",
        "success": False,
        "reason": "Duplicate callback replay detected.",
    }


def test_duplicate_callback_writes_audit_event() -> None:
    container = build_callback_container(callback_shared_secret=None)
    seed_route_transaction(container)

    app.dependency_overrides[get_app_container] = lambda: container

    try:
        client = TestClient(app)
        client.post("/callbacks/mpesa/stk", json=stk_callback_payload())
        response = client.post("/callbacks/mpesa/stk", json=stk_callback_payload())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert container.audit_logger.events[-1].event_type == "stk_callback_duplicate"
    assert container.audit_logger.events[-1].payload["reason"] == (
        "Duplicate callback replay detected."
    )
    assert str(container.audit_logger.events[-1].payload["replay_key"]).startswith(
        "callback_replay:"
    )


def test_disabled_replay_protection_allows_duplicates() -> None:
    container = build_callback_container(
        callback_shared_secret=None,
        callback_replay_protection_enabled=False,
    )
    seed_route_transaction(container)

    app.dependency_overrides[get_app_container] = lambda: container

    try:
        client = TestClient(app)
        first_response = client.post("/callbacks/mpesa/stk", json=stk_callback_payload())
        second_response = client.post("/callbacks/mpesa/stk", json=stk_callback_payload())
    finally:
        app.dependency_overrides.clear()

    assert first_response.status_code == 200
    assert second_response.status_code == 200


def test_duplicate_callback_log_does_not_include_secret(
    caplog: pytest.LogCaptureFixture,
) -> None:
    container = build_callback_container(callback_shared_secret="local-callback-secret")
    seed_route_transaction(container)

    app.dependency_overrides[get_app_container] = lambda: container

    try:
        client = TestClient(app)
        client.post(
            "/callbacks/mpesa/stk",
            json=stk_callback_payload(),
            headers={"X-Callback-Secret": "local-callback-secret"},
        )
        with caplog.at_level(logging.WARNING):
            response = client.post(
                "/callbacks/mpesa/stk",
                json=stk_callback_payload(),
                headers={"X-Callback-Secret": "local-callback-secret"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert "local-callback-secret" not in caplog.text


def build_callback_container(
    callback_shared_secret: str | None,
    *,
    callback_replay_protection_enabled: bool = True,
) -> AppContainer:
    return AppContainer.mock(
        settings=Settings(
            database_url="postgresql+asyncpg://mpesa:mpesa@localhost:5432/mpesa_mcp",
            callback_shared_secret=callback_shared_secret,
            callback_replay_protection_enabled=callback_replay_protection_enabled,
        )
    )


def seed_route_transaction(container: AppContainer) -> None:
    container.transaction_repository.save_pending_transaction(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-001",
        description="Invoice payment",
        checkout_request_id="ws_CO_ROUTE",
        merchant_request_id="mock_123",
    )


def stk_callback_payload() -> dict[str, Any]:
    return {
        "Body": {
            "stkCallback": {
                "CheckoutRequestID": "ws_CO_ROUTE",
                "ResultCode": 0,
                "ResultDesc": "The service request is processed successfully.",
                "CallbackMetadata": {
                    "Item": [
                        {"Name": "Amount", "Value": 1_000},
                        {"Name": "MpesaReceiptNumber", "Value": "RCPROUTE"},
                        {"Name": "PhoneNumber", "Value": 254700000000},
                    ]
                },
            }
        }
    }
