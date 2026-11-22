"""Tests proving MCP agents hold no approval decision authority.

AI agents may request payments, but must never approve or reject them.
"""

from __future__ import annotations

import pytest
from app.bootstrap.container import AppContainer
from app.config import Settings
from app.mcp import server as mcp_server
from app.mcp import tools as mcp_tools
from app.mcp.server import (
    REGISTERED_TOOL_NAMES,
    create_mcp_server,
    create_tool_handlers,
    list_registered_tool_names,
)
from app.policy.tool_policy import AGENT_FORBIDDEN_TOOLS, ToolPolicyEngine
from tests.unit.test_mcp_tools import RecordingPaymentService

DECISION_TOOLS = ("approve_payment_request", "reject_payment_request")
TEST_DATABASE_URL = "postgresql+asyncpg://mpesa:mpesa@localhost:5432/mpesa_mcp"


def test_decision_tools_are_classified_as_agent_forbidden() -> None:
    assert set(DECISION_TOOLS) <= AGENT_FORBIDDEN_TOOLS


def test_mcp_server_does_not_register_approval_decision_tools() -> None:
    container = AppContainer.mock()

    server = create_mcp_server(container=container)
    registered = set(list_registered_tool_names(server))
    handlers = set(create_tool_handlers(container))

    for tool_name in DECISION_TOOLS:
        assert tool_name not in registered
        assert tool_name not in handlers
        assert tool_name not in REGISTERED_TOOL_NAMES


def test_mcp_tools_module_exposes_no_approval_decision_functions() -> None:
    public_names = {name for name in dir(mcp_tools) if not name.startswith("_")}

    assert "approve_payment_request_tool" not in public_names
    assert "reject_payment_request_tool" not in public_names
    assert not any(
        "approve" in name or "reject_payment" in name for name in public_names
    )


def test_no_mcp_handler_can_reach_approval_decisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No registered MCP handler may delegate to an approval decision method."""

    container = AppContainer.mock()
    calls: list[str] = []

    def fail(*_args: object, **_kwargs: object) -> None:
        calls.append("decision")
        raise AssertionError("MCP handler reached an approval decision.")

    monkeypatch.setattr(container.approval_service, "approve_request", fail)
    monkeypatch.setattr(container.approval_service, "reject_request", fail)
    monkeypatch.setattr(container.payment_service, "execute_approved_payment", fail)

    handlers = create_tool_handlers(container)
    handlers["initiate_stk_push"](
        phone_number="254700000000",
        amount=container.settings.max_stk_amount + 1,
        account_reference="INV-AGENT-001",
        description="Agent requested payment",
    )
    handlers["get_today_summary"]()
    handlers["get_failed_transactions"]()
    handlers["run_reconciliation"]()

    assert calls == []


@pytest.mark.parametrize(
    "settings",
    [
        Settings(database_url=TEST_DATABASE_URL),
        Settings(
            database_url=TEST_DATABASE_URL,
            enabled_mcp_tools="approve_payment_request,reject_payment_request",
        ),
        Settings(
            database_url=TEST_DATABASE_URL,
            enabled_mcp_tools="approve_payment_request",
            approval_required_mcp_tools="approve_payment_request",
        ),
        Settings(
            database_url=TEST_DATABASE_URL,
            enabled_mcp_tools="initiate_stk_push,approve_payment_request,reject_payment_request",
            blocked_mcp_tools="",
        ),
    ],
)
def test_policy_configuration_cannot_restore_agent_approval_authority(
    settings: Settings,
) -> None:
    engine = ToolPolicyEngine.from_settings(settings)

    for tool_name in DECISION_TOOLS:
        decision = engine.evaluate(tool_name)
        assert decision.allowed is False
        assert decision.status == "blocked"


def test_direct_policy_construction_cannot_enable_decision_tools() -> None:
    engine = ToolPolicyEngine(enabled_tools=set(DECISION_TOOLS))

    for tool_name in DECISION_TOOLS:
        assert engine.evaluate(tool_name).allowed is False


def test_server_refuses_to_register_forbidden_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = mcp_server.create_tool_handlers

    def handlers_with_forbidden_tool(
        container: AppContainer,
    ) -> dict[str, mcp_server.ToolHandler]:
        handlers = original(container)
        handlers["approve_payment_request"] = lambda **_kwargs: {}
        return handlers

    monkeypatch.setattr(mcp_server, "create_tool_handlers", handlers_with_forbidden_tool)

    with pytest.raises(RuntimeError, match="must never be exposed to MCP agents"):
        create_mcp_server(container=AppContainer.mock())


def test_agent_payment_request_creates_pending_approval_without_provider_call() -> None:
    container = AppContainer.mock()
    handlers = create_tool_handlers(container)

    response = handlers["initiate_stk_push"](
        phone_number="254700000000",
        amount=container.settings.max_stk_amount + 1,
        account_reference="INV-AGENT-002",
        description="Agent requested payment",
    )

    approval = container.approval_service.get_approval_request(response["data"]["approval_id"])
    assert response["status"] == "approval_required"
    assert approval is not None
    assert approval.status == "pending"
    assert approval.reviewer_ids == []
    assert container.transaction_repository.list_transactions() == []


def test_agent_payment_request_is_attributed_to_mcp_agent_not_a_human() -> None:
    container = AppContainer.mock()
    handlers = create_tool_handlers(container)

    handlers["initiate_stk_push"](
        phone_number="254700000000",
        amount=container.settings.max_stk_amount + 1,
        account_reference="INV-AGENT-003",
        description="Agent requested payment",
    )

    created = [
        event for event in container.audit_logger.events if event.event_type == "approval_created"
    ]
    assert len(created) == 1
    assert created[0].actor == "mcp_agent"
    assert created[0].payload["request_origin"] == "mcp_agent"
    assert created[0].operator_subject is None
    assert created[0].operator_roles is None


def test_mcp_initiate_tools_pass_mcp_agent_request_origin() -> None:
    service = RecordingPaymentService()

    mcp_tools.initiate_stk_push_tool(
        {
            "phone_number": "254700000000",
            "amount": 1_000,
            "account_reference": "INV-001",
            "description": "Invoice payment",
        },
        service,
    )

    assert service.request_origin == "mcp_agent"
