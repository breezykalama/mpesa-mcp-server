"""Tests for Daraja client implementations."""

from __future__ import annotations

import json
from typing import Any

import httpx
from app.config import Settings
from app.daraja.client import RealDarajaClient


def sandbox_settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://user:pass@localhost:5432/test",
        daraja_mode="sandbox",
        daraja_consumer_key="consumer-key",
        daraja_consumer_secret="consumer-secret",
        daraja_passkey="passkey",
        daraja_shortcode="174379",
        daraja_callback_url="https://example.test/callback",
    )


def test_real_daraja_client_retrieves_oauth_token_and_initiates_stk_push() -> None:
    requests: list[httpx.Request] = []
    stk_payloads: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/oauth/v1/generate":
            return httpx.Response(200, json={"access_token": "sandbox-token"})

        if request.url.path == "/mpesa/stkpush/v1/processrequest":
            stk_payloads.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(
                200,
                json={
                    "CheckoutRequestID": "ws_CO_123",
                    "MerchantRequestID": "merchant_123",
                    "ResponseCode": "0",
                    "ResponseDescription": "Success. Request accepted for processing.",
                },
            )

        return httpx.Response(404)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = RealDarajaClient(settings=sandbox_settings(), http_client=http_client)

    response = client.initiate_stk_push(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-001",
        description="Invoice payment",
    )

    assert response.checkout_request_id == "ws_CO_123"
    assert response.merchant_request_id == "merchant_123"
    assert requests[0].url.params["grant_type"] == "client_credentials"
    assert requests[1].headers["Authorization"] == "Bearer sandbox-token"
    assert stk_payloads[0]["BusinessShortCode"] == "174379"
    assert stk_payloads[0]["PhoneNumber"] == "254700000000"
    assert stk_payloads[0]["Amount"] == 1_000
    assert stk_payloads[0]["CallBackURL"] == "https://example.test/callback"


def test_real_daraja_transaction_status_is_safe_placeholder() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Transaction status placeholder must not make HTTP calls.")

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = RealDarajaClient(settings=sandbox_settings(), http_client=http_client)

    response = client.check_transaction_status("ws_CO_123")

    assert response.checkout_request_id == "ws_CO_123"
    assert response.status == "unknown"
    assert response.result_code == "PENDING_INTEGRATION"


def test_real_daraja_client_requires_sandbox_credentials() -> None:
    client = RealDarajaClient(
        settings=Settings(
            database_url="postgresql+asyncpg://user:pass@localhost:5432/test",
            daraja_mode="sandbox",
        ),
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(500))
        ),
    )

    try:
        client.initiate_stk_push(
            phone_number="254700000000",
            amount=1_000,
            account_reference="INV-001",
            description="Invoice payment",
        )
    except ValueError as exc:
        assert "DARAJA_CONSUMER_KEY" in str(exc)
    else:
        raise AssertionError("Expected missing sandbox credentials to raise ValueError.")
