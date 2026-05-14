"""Daraja API client interfaces and test doubles."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

import httpx
from pydantic import BaseModel

from app.config import Settings

SAFARICOM_SANDBOX_BASE_URL = "https://sandbox.safaricom.co.ke"
OAUTH_TOKEN_PATH = "/oauth/v1/generate"
STK_PUSH_PATH = "/mpesa/stkpush/v1/processrequest"


class StkPushResponse(BaseModel):
    """Response returned by a Daraja STK push request."""

    checkout_request_id: str
    merchant_request_id: str
    response_code: str
    response_description: str


class TransactionStatusResponse(BaseModel):
    """Response returned by a Daraja transaction status request."""

    checkout_request_id: str
    result_code: str
    result_description: str
    status: str


class DarajaClientProtocol(Protocol):
    """Interface for Daraja API clients."""

    def initiate_stk_push(
        self,
        phone_number: str,
        amount: int,
        account_reference: str,
        description: str,
    ) -> StkPushResponse:
        """Initiate an STK push request."""

    def check_transaction_status(self, checkout_request_id: str) -> TransactionStatusResponse:
        """Check transaction status by checkout request ID."""


class MockDarajaClient:
    """Mock Daraja client for local tests and development."""

    def initiate_stk_push(
        self,
        phone_number: str,
        amount: int,
        account_reference: str,
        description: str,
    ) -> StkPushResponse:
        """Return a fake successful STK push response."""

        return StkPushResponse(
            checkout_request_id=f"ws_CO_{uuid4().hex}",
            merchant_request_id=f"mock_{uuid4().hex}",
            response_code="0",
            response_description="Success. Request accepted for processing.",
        )

    def check_transaction_status(self, checkout_request_id: str) -> TransactionStatusResponse:
        """Return a fake successful transaction status response."""

        return TransactionStatusResponse(
            checkout_request_id=checkout_request_id,
            result_code="0",
            result_description="The service request is processed successfully.",
            status="completed",
        )


class RealDarajaClient:
    """Safaricom Daraja sandbox client."""

    def __init__(
        self,
        *,
        settings: Settings,
        http_client: httpx.Client | None = None,
        base_url: str = SAFARICOM_SANDBOX_BASE_URL,
    ) -> None:
        self._settings = settings
        self._http_client = http_client or httpx.Client(timeout=30.0)
        self._base_url = base_url.rstrip("/")

    def initiate_stk_push(
        self,
        phone_number: str,
        amount: int,
        account_reference: str,
        description: str,
    ) -> StkPushResponse:
        """Initiate a Safaricom Daraja sandbox STK push request."""

        token = self._get_oauth_token()
        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        shortcode = self._required_setting("daraja_shortcode")

        response = self._http_client.post(
            f"{self._base_url}{STK_PUSH_PATH}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "BusinessShortCode": shortcode,
                "Password": self._build_stk_password(timestamp),
                "Timestamp": timestamp,
                "TransactionType": "CustomerPayBillOnline",
                "Amount": amount,
                "PartyA": phone_number,
                "PartyB": shortcode,
                "PhoneNumber": phone_number,
                "CallBackURL": self._required_setting("daraja_callback_url"),
                "AccountReference": account_reference,
                "TransactionDesc": description,
            },
        )
        response.raise_for_status()
        payload = response.json()

        return StkPushResponse(
            checkout_request_id=str(payload.get("CheckoutRequestID", "")),
            merchant_request_id=str(payload.get("MerchantRequestID", "")),
            response_code=str(payload.get("ResponseCode", "")),
            response_description=str(payload.get("ResponseDescription", "")),
        )

    def check_transaction_status(self, checkout_request_id: str) -> TransactionStatusResponse:
        """Return a safe placeholder until Daraja status integration is implemented."""

        return TransactionStatusResponse(
            checkout_request_id=checkout_request_id,
            result_code="PENDING_INTEGRATION",
            result_description="Real Daraja transaction status integration is not implemented yet.",
            status="unknown",
        )

    def _get_oauth_token(self) -> str:
        response = self._http_client.get(
            f"{self._base_url}{OAUTH_TOKEN_PATH}",
            params={"grant_type": "client_credentials"},
            auth=(
                self._required_setting("daraja_consumer_key"),
                self._required_setting("daraja_consumer_secret"),
            ),
        )
        response.raise_for_status()
        access_token = response.json().get("access_token")
        if not isinstance(access_token, str) or access_token == "":
            raise ValueError("Daraja OAuth response did not include an access token.")
        return access_token

    def _build_stk_password(self, timestamp: str) -> str:
        raw_password = (
            self._required_setting("daraja_shortcode")
            + self._required_setting("daraja_passkey")
            + timestamp
        )
        return base64.b64encode(raw_password.encode("utf-8")).decode("utf-8")

    def _required_setting(self, name: str) -> str:
        value = getattr(self._settings, name)
        if not isinstance(value, str) or value == "":
            raise ValueError(f"{name.upper()} is required for Daraja sandbox mode.")
        return value
