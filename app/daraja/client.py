"""Daraja API client interfaces and test doubles."""

from __future__ import annotations

import base64
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol, TypeVar
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

    provider: str = "daraja"
    rail: str = "mpesa"
    checkout_request_id: str
    merchant_request_id: str
    provider_transaction_id: str | None = None
    provider_reference: str | None = None
    response_code: str
    response_description: str
    status: str = "pending"
    error_category: str | None = None
    raw_response: dict[str, object] | None = None


class TransactionStatusResponse(BaseModel):
    """Response returned by a Daraja transaction status request."""

    provider: str = "daraja"
    rail: str = "mpesa"
    checkout_request_id: str
    provider_transaction_id: str | None = None
    provider_reference: str | None = None
    result_code: str
    result_description: str
    status: str
    error_category: str | None = None
    raw_response: dict[str, object] | None = None


class DarajaProviderError(RuntimeError):
    """Normalized Daraja provider failure."""

    def __init__(self, message: str, *, error_category: str = "unknown_error") -> None:
        super().__init__(message)
        self.error_category = error_category


class CircuitBreakerOpenError(DarajaProviderError):
    """Raised when the Daraja circuit breaker is open."""

    def __init__(self) -> None:
        super().__init__(
            "Daraja provider circuit breaker is open.",
            error_category="provider_unavailable",
        )


class DarajaCircuitBreaker:
    """Small deterministic circuit breaker for Daraja provider calls."""

    def __init__(
        self,
        *,
        enabled: bool,
        failure_threshold: int,
        recovery_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._enabled = enabled
        self._failure_threshold = max(1, failure_threshold)
        self._recovery_seconds = recovery_seconds
        self._clock = clock
        self._state = "closed"
        self._failure_count = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> str:
        """Return current circuit state."""

        if not self._enabled:
            return "disabled"
        if self._state == "open" and self._opened_at is not None:
            if self._clock() - self._opened_at >= self._recovery_seconds:
                self._state = "half_open"
        return self._state

    def before_call(self) -> None:
        """Reject calls while the circuit is open."""

        if not self._enabled:
            return
        if self.state == "open":
            raise CircuitBreakerOpenError()

    def record_success(self) -> None:
        """Close the circuit after a successful provider call."""

        if not self._enabled:
            return
        self._state = "closed"
        self._failure_count = 0
        self._opened_at = None

    def record_failure(self) -> None:
        """Track provider failures and open after the configured threshold."""

        if not self._enabled:
            return
        self._failure_count += 1
        if self._failure_count >= self._failure_threshold:
            self._state = "open"
            self._opened_at = self._clock()


T = TypeVar("T")


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
    """Safaricom Daraja sandbox/production client."""

    def __init__(
        self,
        *,
        settings: Settings,
        http_client: httpx.Client | None = None,
        base_url: str | None = None,
        circuit_breaker: DarajaCircuitBreaker | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._http_client = http_client or httpx.Client(
            timeout=settings.daraja_request_timeout_seconds
        )
        self._base_url = (base_url or self._resolve_base_url()).rstrip("/")
        self._sleep = sleep
        self._circuit_breaker = circuit_breaker or DarajaCircuitBreaker(
            enabled=settings.daraja_circuit_breaker_enabled,
            failure_threshold=settings.daraja_circuit_breaker_failure_threshold,
            recovery_seconds=settings.daraja_circuit_breaker_recovery_seconds,
        )
        if settings.daraja_mode == "production":
            self._validate_production_credentials()

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
        self._circuit_breaker.before_call()
        try:
            token = self._get_oauth_token()
        except httpx.HTTPStatusError as exc:
            self._circuit_breaker.record_failure()
            raise DarajaProviderError(
                self._http_error_description(exc),
                error_category=self._error_category_for_status(exc.response.status_code),
            ) from exc
        except httpx.TimeoutException as exc:
            self._circuit_breaker.record_failure()
            raise DarajaProviderError(
                "Daraja OAuth token request timed out.",
                error_category="timeout",
            ) from exc
        except httpx.HTTPError as exc:
            self._circuit_breaker.record_failure()
            raise DarajaProviderError(
                f"Daraja OAuth token request failed: {exc}",
                error_category="provider_unavailable",
            ) from exc
        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        shortcode = self._required_credential("shortcode")
        request_payload = {
            "BusinessShortCode": shortcode,
            "Password": self._build_stk_password(timestamp),
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": amount,
            "PartyA": phone_number,
            "PartyB": shortcode,
            "PhoneNumber": phone_number,
            "CallBackURL": self._required_credential("callback_url"),
            "AccountReference": account_reference,
            "TransactionDesc": description,
        }

        try:
            response = self._http_client.post(
                f"{self._base_url}{STK_PUSH_PATH}",
                headers={"Authorization": f"Bearer {token}"},
                json=request_payload,
                timeout=self._settings.daraja_request_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            self._circuit_breaker.record_failure()
            raise DarajaProviderError(
                self._http_error_description(exc),
                error_category=self._error_category_for_status(exc.response.status_code),
            ) from exc
        except httpx.TimeoutException as exc:
            self._circuit_breaker.record_failure()
            raise DarajaProviderError(
                "Daraja STK push request timed out.",
                error_category="timeout",
            ) from exc
        except httpx.HTTPError as exc:
            self._circuit_breaker.record_failure()
            raise DarajaProviderError(
                f"Daraja STK push request failed: {exc}",
                error_category="provider_unavailable",
            ) from exc
        except ValueError as exc:
            self._circuit_breaker.record_failure()
            raise DarajaProviderError(
                "Daraja STK push response was not valid JSON.",
                error_category="unknown_error",
            ) from exc

        self._circuit_breaker.record_success()
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
            provider_transaction_id=str(payload.get("CheckoutRequestID", "")),
            provider_reference=str(payload.get("MerchantRequestID", "")),
            response_code=str(payload.get("ResponseCode", "")),
            response_description=str(payload.get("ResponseDescription", "")),
            status="pending" if str(payload.get("ResponseCode", "")) == "0" else "failed",
            error_category=None
            if str(payload.get("ResponseCode", "")) == "0"
            else "unknown_error",
            raw_response=self._safe_raw_response(payload),
        )

    def check_transaction_status(self, checkout_request_id: str) -> TransactionStatusResponse:
        """Submit a Safaricom Daraja transaction status query."""

        payload = self._transaction_status_payload(checkout_request_id)

        try:
            self._circuit_breaker.before_call()
            logger.info(
                "Daraja transaction status request started.",
                extra={"event_type": "daraja_transaction_status_query"},
            )
            token = self._get_oauth_token()
            response = self._request_with_retries(
                "POST",
                f"{self._base_url}{TRANSACTION_STATUS_PATH}",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                retry_http_statuses={502, 503, 504},
            )
            response.raise_for_status()
            response_payload = response.json()
            self._circuit_breaker.record_success()
        except httpx.HTTPStatusError as exc:
            self._circuit_breaker.record_failure()
            logger.warning(
                "Daraja transaction status request failed.",
                extra={
                    "event_type": "daraja_request_failed",
                    "status": "failed",
                    "error_category": self._error_category_for_status(
                        exc.response.status_code
                    ),
                },
            )
            return self._failed_transaction_status_response(
                checkout_request_id,
                self._http_error_description(exc),
                error_category=self._error_category_for_status(exc.response.status_code),
            )
        except CircuitBreakerOpenError as exc:
            logger.warning(
                "Daraja transaction status request rejected by circuit breaker.",
                extra={
                    "event_type": "daraja_request_failed",
                    "status": "failed",
                    "error_category": exc.error_category,
                },
            )
            return self._failed_transaction_status_response(
                checkout_request_id,
                str(exc),
                error_category=exc.error_category,
            )
        except httpx.TimeoutException as exc:
            self._circuit_breaker.record_failure()
            logger.warning(
                "Daraja transaction status request timed out.",
                extra={
                    "event_type": "daraja_request_failed",
                    "status": "failed",
                    "error_category": "timeout",
                },
            )
            return self._failed_transaction_status_response(
                checkout_request_id,
                f"Daraja transaction status request timed out: {exc}",
                error_category="timeout",
            )
        except httpx.HTTPError as exc:
            self._circuit_breaker.record_failure()
            logger.warning(
                "Daraja transaction status request failed.",
                extra={
                    "event_type": "daraja_request_failed",
                    "status": "failed",
                    "error_category": "provider_unavailable",
                },
            )
            return self._failed_transaction_status_response(
                checkout_request_id,
                f"Daraja transaction status request failed: {exc}",
                error_category="provider_unavailable",
            )
        except ValueError:
            self._circuit_breaker.record_failure()
            logger.warning(
                "Daraja transaction status response was invalid JSON.",
                extra={
                    "event_type": "daraja_request_failed",
                    "status": "failed",
                    "error_category": "unknown_error",
                },
            )
            return self._failed_transaction_status_response(
                checkout_request_id,
                "Daraja transaction status response was not valid JSON.",
                error_category="unknown_error",
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
            provider_transaction_id=checkout_request_id,
            provider_reference=checkout_request_id,
            result_code=response_code,
            result_description=response_description,
            status="query_accepted" if response_code == "0" else "failed",
            error_category=None if response_code == "0" else "unknown_error",
            raw_response=self._safe_raw_response(response_payload),
        )

    def _get_oauth_token(self) -> str:
        logger.info(
            "Daraja OAuth token request started.",
            extra={"event_type": "daraja_request_started"},
        )
        response = self._request_with_retries(
            "GET",
            f"{self._base_url}{OAUTH_TOKEN_PATH}",
            params={"grant_type": "client_credentials"},
            auth=(
                self._required_credential("consumer_key"),
                self._required_credential("consumer_secret"),
            ),
            retry_http_statuses={502, 503, 504},
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
            self._required_credential("shortcode")
            + self._required_credential("passkey")
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
            "PartyA": self._required_credential("shortcode"),
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
        *,
        error_category: str = "unknown_error",
    ) -> TransactionStatusResponse:
        return TransactionStatusResponse(
            checkout_request_id=checkout_request_id,
            provider_transaction_id=checkout_request_id,
            provider_reference=checkout_request_id,
            result_code="FAILED",
            result_description=reason,
            status="failed",
            error_category=error_category,
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
            raise ValueError(
                f"{name.upper()} is required for Daraja {self._settings.daraja_mode} mode."
            )
        return value

    def _resolve_base_url(self) -> str:
        if self._settings.daraja_mode == "sandbox":
            return self._settings.daraja_sandbox_base_url
        if self._settings.daraja_mode == "production":
            return self._settings.daraja_production_base_url
        return SAFARICOM_SANDBOX_BASE_URL

    def _required_credential(self, credential_name: str) -> str:
        mode = self._settings.daraja_mode
        setting_name = f"daraja_{credential_name}"
        mode_setting_name = f"daraja_{mode}_{credential_name}"
        value = getattr(self._settings, mode_setting_name, None)
        if not isinstance(value, str) or value == "":
            value = getattr(self._settings, setting_name, None)

        if isinstance(value, str) and value != "":
            return value

        if mode == "production":
            raise ValueError(
                f"{mode_setting_name.upper()} or {setting_name.upper()} is required "
                "for Daraja production mode."
            )

        raise ValueError(
            f"{mode_setting_name.upper()} or {setting_name.upper()} is required "
            f"for Daraja {mode} mode."
        )

    def _validate_production_credentials(self) -> None:
        for credential_name in (
            "consumer_key",
            "consumer_secret",
            "passkey",
            "shortcode",
            "callback_url",
        ):
            self._required_credential(credential_name)

    def _request_with_retries(
        self,
        method: str,
        url: str,
        *,
        retry_http_statuses: set[int],
        **kwargs: Any,
    ) -> httpx.Response:
        attempts = max(0, self._settings.daraja_max_retries) + 1
        last_exc: httpx.HTTPError | None = None
        for attempt in range(attempts):
            try:
                response = self._http_client.request(
                    method,
                    url,
                    timeout=self._settings.daraja_request_timeout_seconds,
                    **kwargs,
                )
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc
                if attempt < attempts - 1:
                    self._sleep_between_retries()
                    continue
                raise

            if response.status_code in retry_http_statuses and attempt < attempts - 1:
                self._sleep_between_retries()
                continue

            return response

        if last_exc is not None:
            raise last_exc
        raise DarajaProviderError("Daraja request failed.", error_category="unknown_error")

    def _sleep_between_retries(self) -> None:
        backoff_seconds = self._settings.daraja_retry_backoff_seconds
        if backoff_seconds > 0:
            self._sleep(backoff_seconds)

    def _error_category_for_status(self, status_code: int) -> str:
        if status_code == 400:
            return "validation_error"
        if status_code in {401, 403}:
            return "auth_error"
        if status_code == 408:
            return "timeout"
        if status_code == 429:
            return "rate_limited"
        if status_code in {500, 502, 503, 504}:
            return "provider_unavailable"
        return "unknown_error"

    def _safe_raw_response(self, payload: object) -> dict[str, object] | None:
        if not isinstance(payload, dict):
            return None

        unsafe_keys = {
            "access_token",
            "Password",
            "SecurityCredential",
            "password",
            "securityCredential",
        }
        return {
            str(key): value
            for key, value in payload.items()
            if str(key) not in unsafe_keys
        }
