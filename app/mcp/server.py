"""MCP server setup."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.bootstrap.container import AppContainer, create_app_container
from app.mcp.tools import (
    approve_payment_request_tool,
    check_transaction_status_tool,
    generate_receipt_tool,
    get_failed_transactions_tool,
    get_today_summary_tool,
    initiate_stk_push_tool,
    reject_payment_request_tool,
)

MCP_SERVER_NAME = "mpesa-mcp-server"
REGISTERED_TOOL_NAMES = (
    "initiate_stk_push",
    "check_transaction_status",
    "generate_receipt",
    "get_today_summary",
    "get_failed_transactions",
    "approve_payment_request",
    "reject_payment_request",
)

ToolHandler = Callable[..., dict[str, Any]]
McpDependencies = AppContainer


def create_mcp_dependencies(max_stk_amount: int = 10_000) -> McpDependencies:
    """Create mock-backed dependencies for the MCP server."""

    container = create_app_container()
    if container.settings.max_stk_amount == max_stk_amount:
        return container

    return AppContainer.mock(
        settings=container.settings.model_copy(update={"max_stk_amount": max_stk_amount})
    )


def create_tool_handlers(container: AppContainer) -> dict[str, ToolHandler]:
    """Create MCP tool handlers that delegate to the existing tool wrappers."""

    def initiate_stk_push(
        phone_number: str,
        amount: int,
        account_reference: str,
        description: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        response = initiate_stk_push_tool(
            {
                "phone_number": phone_number,
                "amount": amount,
                "account_reference": account_reference,
                "description": description,
                "idempotency_key": idempotency_key,
            },
            container.payment_service,
        )
        return response.model_dump(mode="json")

    def check_transaction_status(checkout_request_id: str) -> dict[str, Any]:
        response = check_transaction_status_tool(
            {"checkout_request_id": checkout_request_id},
            container.transaction_service,
        )
        return response.model_dump(mode="json")

    def generate_receipt(checkout_request_id: str) -> dict[str, Any]:
        response = generate_receipt_tool(
            {"checkout_request_id": checkout_request_id},
            container.receipt_service,
        )
        return response.model_dump(mode="json")

    def get_today_summary() -> dict[str, Any]:
        response = get_today_summary_tool({}, container.analytics_service)
        return response.model_dump(mode="json")

    def get_failed_transactions() -> dict[str, Any]:
        response = get_failed_transactions_tool({}, container.analytics_service)
        return response.model_dump(mode="json")

    def approve_payment_request(approval_id: str) -> dict[str, Any]:
        response = approve_payment_request_tool(
            {"approval_id": approval_id},
            container.payment_service,
        )
        return response.model_dump(mode="json")

    def reject_payment_request(approval_id: str) -> dict[str, Any]:
        response = reject_payment_request_tool(
            {"approval_id": approval_id},
            container.approval_service,
        )
        return response.model_dump(mode="json")

    return {
        "initiate_stk_push": initiate_stk_push,
        "check_transaction_status": check_transaction_status,
        "generate_receipt": generate_receipt,
        "get_today_summary": get_today_summary,
        "get_failed_transactions": get_failed_transactions,
        "approve_payment_request": approve_payment_request,
        "reject_payment_request": reject_payment_request,
    }


def create_mcp_server(container: AppContainer | None = None) -> FastMCP:
    """Create the M-Pesa MCP server and register tools."""

    resolved_container = container or create_app_container()
    server = FastMCP(MCP_SERVER_NAME)

    for tool_name, handler in create_tool_handlers(resolved_container).items():
        server.tool(name=tool_name)(handler)

    return server


def list_registered_tool_names(server: FastMCP) -> tuple[str, ...]:
    """Return tool names registered on a FastMCP server."""

    tool_manager = server._tool_manager
    tools = tool_manager._tools
    return tuple(tools.keys())


def run() -> None:
    """Run the MCP server."""

    create_mcp_server().run()
