"""Tests for payment provider adapters."""

from __future__ import annotations

from app.daraja.client import MockDarajaClient
from app.payments.providers import DarajaPaymentProvider


def test_daraja_payment_provider_initiates_payment() -> None:
    provider = DarajaPaymentProvider(MockDarajaClient())

    response = provider.initiate_payment(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-001",
        description="Invoice payment",
    )

    assert response.checkout_request_id.startswith("ws_CO_")
    assert response.merchant_request_id.startswith("mock_")
    assert response.response_code == "0"


def test_daraja_payment_provider_checks_transaction_status() -> None:
    provider = DarajaPaymentProvider(MockDarajaClient())

    response = provider.check_transaction_status("ws_CO_123")

    assert response.checkout_request_id == "ws_CO_123"
    assert response.result_code == "0"
    assert response.status == "completed"
