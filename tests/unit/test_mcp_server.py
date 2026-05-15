"""Tests for MCP server setup."""

from __future__ import annotations

from typing import Any

from app.mcp.schemas import McpToolResponse
from app.mcp.server import (
    REGISTERED_TOOL_NAMES,
    create_mcp_dependencies,
    create_mcp_server,
    create_tool_handlers,
    list_registered_tool_names,
)
from pytest import MonkeyPatch


def test_server_can_be_created() -> None:
    server = create_mcp_server()

    assert server is not None


def test_expected_tools_are_registered() -> None:
    server = create_mcp_server()

    assert set(list_registered_tool_names(server)) == set(REGISTERED_TOOL_NAMES)


def test_registered_handler_delegates_to_stk_push_wrapper(monkeypatch: MonkeyPatch) -> None:
    dependencies = create_mcp_dependencies()
    calls: list[dict[str, Any]] = []

    def fake_initiate_stk_push_tool(
        input_data: dict[str, Any],
        payment_service: object,
        **kwargs: object,
    ) -> McpToolResponse:
        calls.append(
            {
                "input_data": input_data,
                "payment_service": payment_service,
                "kwargs": kwargs,
            }
        )
        return McpToolResponse(status="ok", allowed=True, reason="delegated")

    monkeypatch.setattr(
        "app.mcp.server.initiate_stk_push_tool",
        fake_initiate_stk_push_tool,
    )

    handlers = create_tool_handlers(dependencies)
    response = handlers["initiate_stk_push"](
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-001",
        description="Invoice payment",
    )

    assert response["status"] == "ok"
    assert calls[0]["input_data"]["phone_number"] == "254700000000"
    assert calls[0]["payment_service"] is dependencies.payment_service
    assert calls[0]["kwargs"]["rate_limiter"] is dependencies.rate_limiter


def test_registered_handler_delegates_to_today_summary_wrapper(monkeypatch: MonkeyPatch) -> None:
    dependencies = create_mcp_dependencies()
    calls: list[object] = []

    def fake_get_today_summary_tool(
        input_data: dict[str, Any],
        analytics_service: object,
        **kwargs: object,
    ) -> McpToolResponse:
        calls.append({"analytics_service": analytics_service, "kwargs": kwargs})
        return McpToolResponse(
            status="ok",
            allowed=True,
            reason="delegated",
            data={"summary": {"total_revenue": 0}},
        )

    monkeypatch.setattr(
        "app.mcp.server.get_today_summary_tool",
        fake_get_today_summary_tool,
    )

    handlers = create_tool_handlers(dependencies)
    response = handlers["get_today_summary"]()

    assert response["data"]["summary"]["total_revenue"] == 0
    assert calls == [
        {
            "analytics_service": dependencies.analytics_service,
            "kwargs": {"tool_policy": dependencies.tool_policy},
        }
    ]
