"""Tests for MCP tool governance policy."""

from __future__ import annotations

from app.config import Settings
from app.policy.tool_policy import ToolPolicyEngine, parse_tool_list


def test_config_parsing_works() -> None:
    assert parse_tool_list("initiate_stk_push, generate_receipt,, ") == {
        "initiate_stk_push",
        "generate_receipt",
    }


def test_policy_defaults_allow_current_tools() -> None:
    engine = ToolPolicyEngine.from_settings(Settings(database_url="sqlite://"))

    decision = engine.evaluate("initiate_stk_push")

    assert decision.allowed is True
    assert decision.status == "allowed"


def test_blocked_tool_denied() -> None:
    engine = ToolPolicyEngine.from_settings(
        Settings(
            database_url="sqlite://",
            blocked_mcp_tools="initiate_stk_push",
        )
    )

    decision = engine.evaluate("initiate_stk_push")

    assert decision.allowed is False
    assert decision.status == "blocked"
    assert decision.reason == "Tool disabled by policy"


def test_enabled_tool_list_restricts_other_tools() -> None:
    engine = ToolPolicyEngine.from_settings(
        Settings(
            database_url="sqlite://",
            enabled_mcp_tools="get_today_summary",
        )
    )

    assert engine.evaluate("get_today_summary").allowed is True
    assert engine.evaluate("initiate_stk_push").status == "blocked"


def test_approval_required_tool_returns_approval_required() -> None:
    engine = ToolPolicyEngine.from_settings(
        Settings(
            database_url="sqlite://",
            approval_required_mcp_tools="initiate_stk_push",
        )
    )

    decision = engine.evaluate("initiate_stk_push")

    assert decision.allowed is False
    assert decision.status == "approval_required"
    assert decision.requires_approval is True
    assert decision.reason == "Tool requires approval by policy"


def test_approval_flow_tools_are_not_forced_into_approval_required() -> None:
    engine = ToolPolicyEngine.from_settings(
        Settings(
            database_url="sqlite://",
            approval_required_mcp_tools="approve_payment_request",
        )
    )

    decision = engine.evaluate("approve_payment_request")

    assert decision.allowed is True
