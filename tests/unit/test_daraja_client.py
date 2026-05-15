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
        daraja_initiator_name="testapi",
        daraja_security_credential="encrypted-credential",
        daraja_transaction_status_result_url="https://example.test/status/result",
        daraja_transaction_status_timeout_url="https://example.test/status/timeout",
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


def test_real_daraja_transaction_status_sends_expected_request() -> None:
    requests: list[httpx.Request] = []
    status_payloads: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/oauth/v1/generate":
            return httpx.Response(200, json={"access_token": "sandbox-token"})

        if request.url.path == "/mpesa/transactionstatus/v1/query":
            status_payloads.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(
                200,
                json={
                    "ResponseCode": "0",
                    "ResponseDescription": "Accept the service request successfully.",
                },
            )

        return httpx.Response(404)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = RealDarajaClient(settings=sandbox_settings(), http_client=http_client)

    response = client.check_transaction_status("ws_CO_123")

    assert response.checkout_request_id == "ws_CO_123"
    assert response.status == "query_accepted"
    assert response.result_code == "0"
    assert requests[1].url.path == "/mpesa/transactionstatus/v1/query"
    assert requests[1].headers["Authorization"] == "Bearer sandbox-token"
    assert status_payloads[0]["CommandID"] == "TransactionStatusQuery"
    assert status_payloads[0]["TransactionID"] == "ws_CO_123"
    assert status_payloads[0]["PartyA"] == "174379"
    assert status_payloads[0]["IdentifierType"] == 4


def test_real_daraja_transaction_status_non_zero_response_maps_to_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/v1/generate":
            return httpx.Response(200, json={"access_token": "sandbox-token"})

        return httpx.Response(
            200,
            json={
                "ResponseCode": "1",
                "ResponseDescription": "Request failed.",
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = RealDarajaClient(settings=sandbox_settings(), http_client=http_client)

    response = client.check_transaction_status("LKXXXX1234")

    assert response.status == "failed"
    assert response.result_code == "1"
    assert response.result_description == "Request failed."


def test_real_daraja_transaction_status_missing_credentials_raises_cleanly() -> None:
    client = RealDarajaClient(
        settings=Settings(
            database_url="postgresql+asyncpg://user:pass@localhost:5432/test",
            daraja_mode="sandbox",
            daraja_consumer_key="consumer-key",
            daraja_consumer_secret="consumer-secret",
            daraja_shortcode="174379",
        ),
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(500))
        ),
    )

    try:
        client.check_transaction_status("LKXXXX1234")
    except ValueError as exc:
        assert "DARAJA_INITIATOR_NAME" in str(exc)
    else:
        raise AssertionError("Expected missing transaction status credentials to raise.")


def test_real_daraja_transaction_status_network_failure_returns_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/v1/generate":
            return httpx.Response(200, json={"access_token": "sandbox-token"})

        raise httpx.ConnectError("network unavailable", request=request)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = RealDarajaClient(settings=sandbox_settings(), http_client=http_client)

    response = client.check_transaction_status("LKXXXX1234")

    assert response.status == "failed"
    assert response.result_code == "FAILED"
    assert "network unavailable" in response.result_description


def test_real_daraja_transaction_status_http_failure_returns_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/v1/generate":
            return httpx.Response(200, json={"access_token": "sandbox-token"})

        return httpx.Response(500, request=request, json={"errorMessage": "Server error."})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = RealDarajaClient(settings=sandbox_settings(), http_client=http_client)

    response = client.check_transaction_status("LKXXXX1234")

    assert response.status == "failed"
    assert response.result_code == "FAILED"
    assert "Server error" in response.result_description


def test_real_daraja_transaction_status_invalid_json_returns_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/v1/generate":
            return httpx.Response(200, json={"access_token": "sandbox-token"})

        return httpx.Response(200, content=b"not-json")

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = RealDarajaClient(settings=sandbox_settings(), http_client=http_client)

    response = client.check_transaction_status("LKXXXX1234")

    assert response.status == "failed"
    assert response.result_code == "FAILED"
    assert response.result_description == "Daraja transaction status response was not valid JSON."


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
