"""Integration tests for the minimal operator UI."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from app.bootstrap.container import AppContainer
from app.callbacks.routes import get_app_container
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def validated_container() -> Iterator[None]:
    # Every route, including static HTML, sits behind the startup safety gate, so the
    # test provides an explicit container instead of the unvalidated environment.
    app.dependency_overrides[get_app_container] = AppContainer.mock
    yield
    app.dependency_overrides.clear()


def test_operator_ui_returns_html() -> None:
    client = TestClient(app)

    response = client.get("/operator/ui")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>M-Pesa MCP Operator Console</title>" in response.text


def test_operator_ui_does_not_contain_secrets() -> None:
    client = TestClient(app)

    response = client.get("/operator/ui")

    assert "OPERATOR_VIEWER_TOKEN" not in response.text
    assert "OPERATOR_APPROVER_TOKEN" not in response.text
    assert "OPERATOR_ADMIN_TOKEN" not in response.text
    assert "CALLBACK_SHARED_SECRET" not in response.text
    assert "DARAJA_CONSUMER_SECRET" not in response.text
    assert "DARAJA_PASSKEY" not in response.text


def test_operator_ui_includes_expected_sections() -> None:
    client = TestClient(app)

    response = client.get("/operator/ui")

    assert "Today Analytics Summary" in response.text
    assert "Recent Transactions" in response.text
    assert "Pending Approvals" in response.text
    assert "Recent Audit Events" in response.text
    assert "Run reconciliation" in response.text
    assert "Operator token" in response.text
    assert "/operator/analytics/today" in response.text
    assert "/operator/transactions" in response.text
    assert "/operator/audit-events" in response.text
    assert "/approvals/pending" in response.text
    assert "/operator/reconciliation/run" in response.text
