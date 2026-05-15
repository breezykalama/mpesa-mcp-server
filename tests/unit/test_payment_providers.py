"""Tests for payment provider adapters."""

from __future__ import annotations

from app.daraja.client import MockDarajaClient
from app.payments.providers import AirtelMoneyMockProvider, DarajaPaymentProvider


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
    assert response.provider == "daraja"
    assert response.rail == "mpesa"
    assert response.provider_transaction_id == response.checkout_request_id
    assert response.provider_reference == response.merchant_request_id
    assert response.response_code == "0"


def test_daraja_payment_provider_checks_transaction_status() -> None:
    provider = DarajaPaymentProvider(MockDarajaClient())

    response = provider.check_transaction_status("ws_CO_123")

    assert response.checkout_request_id == "ws_CO_123"
    assert response.provider == "daraja"
    assert response.rail == "mpesa"
    assert response.provider_transaction_id == "ws_CO_123"
    assert response.provider_reference == "ws_CO_123"
    assert response.result_code == "0"


def test_airtel_money_mock_provider_initiates_payment() -> None:
    provider = AirtelMoneyMockProvider()

    response = provider.initiate_payment(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-AIRTEL-001",
        description="Invoice payment",
    )

    assert response.provider == "airtel"
    assert response.rail == "airtel_money"
    assert response.provider_transaction_id is not None
    assert response.provider_transaction_id.startswith("airtel_txn_")
    assert response.provider_reference is not None
    assert response.provider_reference.startswith("airtel_ref_")
    assert response.checkout_request_id == response.provider_transaction_id
    assert response.merchant_request_id == response.provider_reference


def test_airtel_money_mock_provider_checks_transaction_status() -> None:
    provider = AirtelMoneyMockProvider()

    response = provider.check_transaction_status("airtel_txn_123")

    assert response.provider == "airtel"
    assert response.rail == "airtel_money"
    assert response.provider_transaction_id == "airtel_txn_123"
    assert response.status == "query_accepted"
