"""Configurable MCP tool governance policy."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings

CURRENT_MCP_TOOLS = frozenset(
    {
        "initiate_stk_push",
        "check_transaction_status",
        "generate_receipt",
        "get_today_summary",
        "get_failed_transactions",
        "approve_payment_request",
        "reject_payment_request",
    }
)

APPROVAL_FLOW_TOOLS = frozenset(
    {
        "approve_payment_request",
        "reject_payment_request",
    }
)


@dataclass(frozen=True)
class ToolPolicyDecision:
    """Decision returned by MCP tool governance policy."""

    status: str
    allowed: bool
    reason: str
    requires_approval: bool = False


class ToolPolicyEngine:
    """Evaluate configurable MCP tool governance rules."""

    def __init__(
        self,
        *,
        enabled_tools: set[str] | None = None,
        blocked_tools: set[str] | None = None,
        approval_required_tools: set[str] | None = None,
    ) -> None:
        self._enabled_tools = enabled_tools or set(CURRENT_MCP_TOOLS)
        self._blocked_tools = blocked_tools or set()
        self._approval_required_tools = approval_required_tools or set()

    @classmethod
    def from_settings(cls, settings: Settings) -> ToolPolicyEngine:
        """Build a tool policy engine from comma-separated settings."""

        enabled_tools = parse_tool_list(settings.enabled_mcp_tools)
        return cls(
            enabled_tools=enabled_tools if enabled_tools else set(CURRENT_MCP_TOOLS),
            blocked_tools=parse_tool_list(settings.blocked_mcp_tools),
            approval_required_tools=parse_tool_list(settings.approval_required_mcp_tools),
        )

    def evaluate(self, tool_name: str) -> ToolPolicyDecision:
        """Evaluate whether a tool can execute."""

        if tool_name in self._blocked_tools or tool_name not in self._enabled_tools:
            return ToolPolicyDecision(
                status="blocked",
                allowed=False,
                reason="Tool disabled by policy",
            )

        if (
            tool_name in self._approval_required_tools
            and tool_name not in APPROVAL_FLOW_TOOLS
        ):
            return ToolPolicyDecision(
                status="approval_required",
                allowed=False,
                reason="Tool requires approval by policy",
                requires_approval=True,
            )

        return ToolPolicyDecision(
            status="allowed",
            allowed=True,
            reason="Tool allowed by policy",
        )


def parse_tool_list(value: str) -> set[str]:
    """Parse a comma-separated tool list."""

    return {item.strip() for item in value.split(",") if item.strip()}
