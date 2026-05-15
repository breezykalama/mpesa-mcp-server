"""Daraja API client interfaces and test doubles."""

from __future__ import annotations

import base64
import logging
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

import httpx
from pydantic import BaseModel

from app.config import Settings

logger = logging.getLogger(__name__)

SAFARICOM_SANDBOX_BASE_URL = "https://sandbox.safaricom.co.ke"
SAFARICOM_PRODUCTION_BASE_URL = "https://api.safaricom.co.ke"
OAUTH_TOKEN_PATH = "/oauth/v1/generate"
STK_PUSH_PATH = "/mpesa/stkpush/v1/processrequest"
TRANSACTION_STATUS_PATH = "/mpesa/transactionstatus/v1/query"


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

        logger.info(
            "Daraja STK push request started.",
            extra={"event_type": "daraja_request_started", "amount": amount},
        )
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
        logger.info(
            "Daraja STK push request completed.",
            extra={
                "event_type": "daraja_request_completed",
                "status": str(payload.get("ResponseCode", "")),
            },
        )

        return StkPushResponse(
            checkout_request_id=str(payload.get("CheckoutRequestID", "")),
            merchant_request_id=str(payload.get("MerchantRequestID", "")),
            response_code=str(payload.get("ResponseCode", "")),
            response_description=str(payload.get("ResponseDescription", "")),
        )

    def check_transaction_status(self, checkout_request_id: str) -> TransactionStatusResponse:
        """Submit a Safaricom Daraja sandbox transaction status query."""

        payload = self._transaction_status_payload(checkout_request_id)

        try:
            logger.info(
                "Daraja transaction status request started.",
                extra={"event_type": "daraja_transaction_status_query"},
            )
            token = self._get_oauth_token()
            response = self._http_client.post(
                f"{self._base_url}{TRANSACTION_STATUS_PATH}",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            response.raise_for_status()
            response_payload = response.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Daraja transaction status request failed.",
                extra={"event_type": "daraja_request_failed", "status": "failed"},
            )
            return self._failed_transaction_status_response(
                checkout_request_id,
                self._http_error_description(exc),
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "Daraja transaction status request failed.",
                extra={"event_type": "daraja_request_failed", "status": "failed"},
            )
            return self._failed_transaction_status_response(
                checkout_request_id,
                f"Daraja transaction status request failed: {exc}",
            )
        except ValueError:
            logger.warning(
                "Daraja transaction status response was invalid JSON.",
                extra={"event_type": "daraja_request_failed", "status": "failed"},
            )
            return self._failed_transaction_status_response(
                checkout_request_id,
                "Daraja transaction status response was not valid JSON.",
            )

        response_code = str(response_payload.get("ResponseCode", ""))
        response_description = str(
            response_payload.get("ResponseDescription")
            or response_payload.get("errorMessage")
            or "Daraja transaction status query failed."
        )

        logger.info(
            "Daraja transaction status request completed.",
            extra={
                "event_type": "daraja_transaction_status_query",
                "status": "query_accepted" if response_code == "0" else "failed",
            },
        )
        return TransactionStatusResponse(
            checkout_request_id=checkout_request_id,
            result_code=response_code,
            result_description=response_description,
            status="query_accepted" if response_code == "0" else "failed",
        )

    def _get_oauth_token(self) -> str:
        logger.info(
            "Daraja OAuth token request started.",
            extra={"event_type": "daraja_request_started"},
        )
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
            logger.warning(
                "Daraja OAuth response did not include token.",
                extra={"event_type": "daraja_request_failed", "status": "failed"},
            )
            raise ValueError("Daraja OAuth response did not include an access token.")
        logger.info(
            "Daraja OAuth token request completed.",
            extra={"event_type": "daraja_request_completed"},
        )
        return access_token

    def _build_stk_password(self, timestamp: str) -> str:
        raw_password = (
            self._required_setting("daraja_shortcode")
            + self._required_setting("daraja_passkey")
            + timestamp
        )
        return base64.b64encode(raw_password.encode("utf-8")).decode("utf-8")

    def _transaction_status_payload(self, checkout_request_id: str) -> dict[str, str | int]:
        # TODO: Daraja Transaction Status expects an M-Pesa transaction ID or suitable
        # Daraja transaction reference. For compatibility, this method currently accepts
        # the existing checkout_request_id argument and sends it as TransactionID.
        return {
            "Initiator": self._required_setting("daraja_initiator_name"),
            "SecurityCredential": self._required_setting("daraja_security_credential"),
            "CommandID": "TransactionStatusQuery",
            "TransactionID": checkout_request_id,
            "PartyA": self._required_setting("daraja_shortcode"),
            "IdentifierType": self._settings.daraja_identifier_type,
            "ResultURL": self._required_setting("daraja_transaction_status_result_url"),
            "QueueTimeOutURL": self._required_setting(
                "daraja_transaction_status_timeout_url"
            ),
            "Remarks": self._settings.daraja_transaction_status_remarks,
            "Occasion": self._settings.daraja_transaction_status_occasion,
        }

    def _failed_transaction_status_response(
        self,
        checkout_request_id: str,
        reason: str,
    ) -> TransactionStatusResponse:
        return TransactionStatusResponse(
            checkout_request_id=checkout_request_id,
            result_code="FAILED",
            result_description=reason,
            status="failed",
        )

    def _http_error_description(self, exc: httpx.HTTPStatusError) -> str:
        try:
            payload = exc.response.json()
        except ValueError:
            return f"Daraja transaction status request failed: {exc}"

        error_message = payload.get("errorMessage")
        if isinstance(error_message, str) and error_message != "":
            return error_message

        return f"Daraja transaction status request failed: {exc}"

    def _required_setting(self, name: str) -> str:
        value = getattr(self._settings, name)
        if not isinstance(value, str) or value == "":
            raise ValueError(f"{name.upper()} is required for Daraja sandbox mode.")
        return value
