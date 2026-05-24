"""Tests for transaction repository state transition enforcement."""

from __future__ import annotations

import pytest
from app.storage.repositories import InMemoryTransactionRepository
from app.transactions.state_machine import TransactionStateTransitionError


def test_in_memory_repository_allows_pending_to_timed_out() -> None:
    repository = build_repository_with_pending_transaction()

    transaction = repository.update_transaction_status(
        checkout_request_id="ws_CO_STATE",
        status="timed_out",
        result_code=408,
        result_description="Timed out",
    )

    assert transaction is not None
    assert transaction.status == "timed_out"


def test_in_memory_repository_allows_pending_to_cancelled() -> None:
    repository = build_repository_with_pending_transaction()

    transaction = repository.update_transaction_status(
        checkout_request_id="ws_CO_STATE",
        status="cancelled",
        result_code=1032,
        result_description="Cancelled",
    )

    assert transaction is not None
    assert transaction.status == "cancelled"


def test_in_memory_repository_does_not_mutate_on_invalid_transition() -> None:
    repository = build_repository_with_pending_transaction()
    repository.update_transaction_status(
        checkout_request_id="ws_CO_STATE",
        status="completed",
        result_code=0,
        result_description="Success",
        mpesa_receipt_number="RCP123",
    )

    with pytest.raises(TransactionStateTransitionError):
        repository.update_transaction_status(
            checkout_request_id="ws_CO_STATE",
            status="failed",
            result_code=1032,
            result_description="Late failure",
            mpesa_receipt_number=None,
        )

    transaction = repository.find_by_checkout_request_id("ws_CO_STATE")

    assert transaction is not None
    assert transaction.status == "completed"
    assert transaction.result_description == "Success"
    assert transaction.mpesa_receipt_number == "RCP123"


def build_repository_with_pending_transaction() -> InMemoryTransactionRepository:
    repository = InMemoryTransactionRepository()
    repository.save_pending_transaction(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-STATE",
        description="Invoice payment",
        checkout_request_id="ws_CO_STATE",
        merchant_request_id="mock_state",
    )
    return repository

