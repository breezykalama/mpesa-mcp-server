"""MCP schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class InitiateStkPushInput(BaseModel):
    """Input schema for the initiate_stk_push MCP tool."""

    phone_number: str
    amount: int
    account_reference: str
    description: str
    idempotency_key: str | None = None


class InitiatePaymentInput(BaseModel):
    """Input schema for the provider-agnostic initiate_payment MCP tool."""

    phone_number: str
    amount: int
    account_reference: str
    description: str
    idempotency_key: str | None = None


class CheckTransactionStatusInput(BaseModel):
    """Input schema for the check_transaction_status MCP tool."""

    checkout_request_id: str


class CheckPaymentStatusInput(BaseModel):
    """Input schema for the provider-agnostic check_payment_status MCP tool."""

    provider_transaction_id: str


class GenerateReceiptInput(BaseModel):
    """Input schema for the generate_receipt MCP tool."""

    checkout_request_id: str


class GetTodaySummaryInput(BaseModel):
    """Input schema for the get_today_summary MCP tool."""


class GetFailedTransactionsInput(BaseModel):
    """Input schema for the get_failed_transactions MCP tool."""


class ApprovePaymentRequestInput(BaseModel):
    """Input schema for the approve_payment_request MCP tool."""

    approval_id: str


class RejectPaymentRequestInput(BaseModel):
    """Input schema for the reject_payment_request MCP tool."""

    approval_id: str


class RunReconciliationInput(BaseModel):
    """Input schema for the run_reconciliation MCP tool."""

    model_config = ConfigDict(extra="forbid")


class McpToolResponse(BaseModel):
    """Structured MCP tool response."""

    status: str
    allowed: bool
    reason: str
    requires_approval: bool = False
    data: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
