"""Integration tests for the minimal operator UI."""

from __future__ import annotations

from app.main import app
from fastapi.testclient import TestClient


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
