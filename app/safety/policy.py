"""Payment safety policy."""

from __future__ import annotations

from pydantic import BaseModel

READ_ONLY_ACTIONS = {
    "check_transaction_status",
    "get_today_transactions",
    "get_failed_transactions",
    "generate_receipt",
}

INITIATE_STK_PUSH = "initiate_stk_push"


class PaymentActionRequest(BaseModel):
    """Payment action request evaluated by the safety policy."""

    action: str
    amount: int | None = None
    phone_number: str | None = None
    recipient: str | None = None


class PolicyDecision(BaseModel):
    """Decision returned by the payment safety policy."""

    status: str
    allowed: bool
    reason: str
    requires_approval: bool = False


class PaymentPolicy:
    """Evaluate whether payment actions are allowed."""

    def __init__(self, max_stk_amount: int) -> None:
        self.max_stk_amount = max_stk_amount

    def evaluate(self, request: PaymentActionRequest) -> PolicyDecision:
        """Evaluate a payment action request."""

        if request.action in READ_ONLY_ACTIONS:
            return PolicyDecision(
                status="allowed",
                allowed=True,
                reason="Read-only action is allowed.",
            )

        if request.action == INITIATE_STK_PUSH:
            return self._evaluate_stk_push(request)

        return PolicyDecision(
            status="blocked",
            allowed=False,
            reason="Unknown action is blocked.",
        )

    def _evaluate_stk_push(self, request: PaymentActionRequest) -> PolicyDecision:
        if request.amount is None:
            return PolicyDecision(
                status="blocked",
                allowed=False,
                reason="Amount is required for STK push.",
            )

        if request.phone_number is None:
            return PolicyDecision(
                status="blocked",
                allowed=False,
                reason="Phone number is required for STK push.",
            )

        if request.amount <= 0:
            return PolicyDecision(
                status="blocked",
                allowed=False,
                reason="Amount must be greater than 0.",
            )

        if request.amount > self.max_stk_amount:
            return PolicyDecision(
                status="approval_required",
                allowed=False,
                reason="Amount exceeds the configured STK push limit.",
                requires_approval=True,
            )

        return PolicyDecision(
            status="allowed",
            allowed=True,
            reason="STK push is within the configured limit.",
        )
