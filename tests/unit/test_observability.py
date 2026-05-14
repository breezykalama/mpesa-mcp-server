"""Tests for observability metrics and health routes."""

from __future__ import annotations

from app.bootstrap.container import AppContainer
from app.callbacks.handlers import StkCallbackHandler
from app.callbacks.routes import get_app_container
from app.main import app
from app.storage.repositories import InMemoryTransactionRepository
from fastapi.testclient import TestClient
from tests.unit.test_receipt_service import seed_transaction
from tests.unit.test_stk_callback_handler import stk_callback_payload


def test_metrics_increment_on_stk_push() -> None:
    container = AppContainer.mock()

    response = container.payment_service.initiate_stk_push(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-001",
        description="Invoice payment",
    )

    metrics = container.metrics_recorder.snapshot()
    assert response.status == "pending"
    assert metrics.successful_payment_count == 1


def test_metrics_increment_on_approval_required() -> None:
    container = AppContainer.mock()

    response = container.payment_service.initiate_stk_push(
        phone_number="254700000000",
        amount=10_001,
        account_reference="INV-002",
        description="Invoice payment",
    )

    metrics = container.metrics_recorder.snapshot()
    assert response.status == "approval_required"
    assert metrics.approval_required_count == 1


def test_metrics_increment_on_callback_received() -> None:
    container = AppContainer.mock()
    container.transaction_repository.save_pending_transaction(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-003",
        description="Invoice payment",
        checkout_request_id="ws_CO_123",
        merchant_request_id="mock_123",
    )

    callback_handler = StkCallbackHandler(
        transaction_repository=container.transaction_repository,
        audit_logger=container.audit_logger,
        metrics_recorder=container.metrics_recorder,
    )
    callback_handler.process(stk_callback_payload())

    metrics = container.metrics_recorder.snapshot()
    assert metrics.callback_received_count == 1


def test_metrics_increment_on_receipt_generated() -> None:
    container = AppContainer.mock()
    assert isinstance(container.transaction_repository, InMemoryTransactionRepository)
    seed_transaction(container.transaction_repository)

    response = container.receipt_service.generate_receipt("ws_CO_123")

    metrics = container.metrics_recorder.snapshot()
    assert response.status == "generated"
    assert metrics.receipt_generated_count == 1


def test_health_endpoint_works() -> None:
    container = AppContainer.mock()
    app.dependency_overrides[get_app_container] = lambda: container

    try:
        response = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "storage_mode": "memory"}


def test_readiness_endpoint_works() -> None:
    container = AppContainer.mock()
    app.dependency_overrides[get_app_container] = lambda: container

    try:
        response = TestClient(app).get("/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["ready"] is True
    assert body["storage_mode"] == "memory"


def test_metrics_endpoint_returns_expected_counters() -> None:
    container = AppContainer.mock()
    container.metrics_recorder.increment("successful_payment_count", 2)
    container.metrics_recorder.increment("callback_received_count")
    app.dependency_overrides[get_app_container] = lambda: container

    try:
        response = TestClient(app).get("/metrics")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["successful_payment_count"] == 2
    assert body["callback_received_count"] == 1
    assert body["approval_required_count"] == 0
