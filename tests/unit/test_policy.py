"""Tests for payment safety policy decisions."""

from app.safety.policy import PaymentActionRequest, PaymentPolicy


def test_read_only_action_allowed() -> None:
    policy = PaymentPolicy(max_stk_amount=10_000)

    decision = policy.evaluate(PaymentActionRequest(action="check_transaction_status"))

    assert decision.status == "allowed"
    assert decision.allowed is True
    assert decision.requires_approval is False


def test_unknown_action_blocked() -> None:
    policy = PaymentPolicy(max_stk_amount=10_000)

    decision = policy.evaluate(PaymentActionRequest(action="refund_payment"))

    assert decision.status == "blocked"
    assert decision.allowed is False
    assert decision.requires_approval is False


def test_stk_push_missing_amount_blocked() -> None:
    policy = PaymentPolicy(max_stk_amount=10_000)

    decision = policy.evaluate(
        PaymentActionRequest(action="initiate_stk_push", phone_number="254700000000")
    )

    assert decision.status == "blocked"
    assert decision.allowed is False
    assert "Amount is required" in decision.reason


def test_stk_push_missing_phone_blocked() -> None:
    policy = PaymentPolicy(max_stk_amount=10_000)

    decision = policy.evaluate(PaymentActionRequest(action="initiate_stk_push", amount=1_000))

    assert decision.status == "blocked"
    assert decision.allowed is False
    assert "Phone number is required" in decision.reason


def test_stk_push_amount_within_limit_allowed() -> None:
    policy = PaymentPolicy(max_stk_amount=10_000)

    decision = policy.evaluate(
        PaymentActionRequest(
            action="initiate_stk_push",
            amount=10_000,
            phone_number="254700000000",
        )
    )

    assert decision.status == "allowed"
    assert decision.allowed is True
    assert decision.requires_approval is False


def test_stk_push_amount_above_limit_requires_approval() -> None:
    policy = PaymentPolicy(max_stk_amount=10_000)

    decision = policy.evaluate(
        PaymentActionRequest(
            action="initiate_stk_push",
            amount=10_001,
            phone_number="254700000000",
        )
    )

    assert decision.status == "approval_required"
    assert decision.allowed is False
    assert decision.requires_approval is True


def test_stk_push_negative_amount_blocked() -> None:
    policy = PaymentPolicy(max_stk_amount=10_000)

    decision = policy.evaluate(
        PaymentActionRequest(
            action="initiate_stk_push",
            amount=-1,
            phone_number="254700000000",
        )
    )

    assert decision.status == "blocked"
    assert decision.allowed is False
    assert "greater than 0" in decision.reason
