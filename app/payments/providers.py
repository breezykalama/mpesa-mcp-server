"""Payment provider abstractions and adapters."""

from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel

from app.daraja.client import DarajaClientProtocol


class PaymentInitiationResponse(BaseModel):
    """Generic payment initiation response."""

    provider: str = "daraja"
    rail: str = "mpesa"
    checkout_request_id: str
    merchant_request_id: str
    provider_transaction_id: str | None = None
    provider_reference: str | None = None
    response_code: str
    response_description: str


class PaymentStatusResponse(BaseModel):
    """Generic payment status response."""

    provider: str = "daraja"
    rail: str = "mpesa"
    checkout_request_id: str
    provider_transaction_id: str | None = None
    provider_reference: str | None = None
    result_code: str
    result_description: str
    status: str


class PaymentProviderProtocol(Protocol):
    """Generic payment provider interface."""

    def initiate_payment(
        self,
        *,
        phone_number: str,
        amount: int,
        account_reference: str,
        description: str,
    ) -> PaymentInitiationResponse:
        """Initiate a payment request."""

    def check_transaction_status(self, transaction_reference: str) -> PaymentStatusResponse:
        """Check payment status by provider transaction reference."""


class DarajaPaymentProvider:
    """Payment provider adapter backed by an existing Daraja client."""

    def __init__(self, daraja_client: DarajaClientProtocol) -> None:
        self._daraja_client = daraja_client

    def initiate_payment(
        self,
        *,
        phone_number: str,
        amount: int,
        account_reference: str,
        description: str,
    ) -> PaymentInitiationResponse:
        """Initiate payment through Daraja STK Push."""

        response = self._daraja_client.initiate_stk_push(
            phone_number=phone_number,
            amount=amount,
            account_reference=account_reference,
            description=description,
        )
        return PaymentInitiationResponse(
            provider="daraja",
            rail="mpesa",
            checkout_request_id=response.checkout_request_id,
            merchant_request_id=response.merchant_request_id,
            provider_transaction_id=response.checkout_request_id,
            provider_reference=response.merchant_request_id,
            response_code=response.response_code,
            response_description=response.response_description,
        )

    def check_transaction_status(self, transaction_reference: str) -> PaymentStatusResponse:
        """Check transaction status through Daraja."""

        response = self._daraja_client.check_transaction_status(transaction_reference)
        return PaymentStatusResponse(
            provider="daraja",
            rail="mpesa",
            checkout_request_id=response.checkout_request_id,
            provider_transaction_id=response.checkout_request_id,
            provider_reference=transaction_reference,
            result_code=response.result_code,
            result_description=response.result_description,
            status=response.status,
        )


class AirtelMoneyMockProvider:
    """Mock Airtel Money provider proving the multi-rail provider abstraction."""

    def initiate_payment(
        self,
        *,
        phone_number: str,
        amount: int,
        account_reference: str,
        description: str,
    ) -> PaymentInitiationResponse:
        """Return a fake Airtel Money payment initiation response."""

        provider_transaction_id = f"airtel_txn_{uuid4().hex}"
        provider_reference = f"airtel_ref_{uuid4().hex}"
        return PaymentInitiationResponse(
            provider="airtel",
            rail="airtel_money",
            checkout_request_id=provider_transaction_id,
            merchant_request_id=provider_reference,
            provider_transaction_id=provider_transaction_id,
            provider_reference=provider_reference,
            response_code="0",
            response_description="Airtel Money mock payment accepted for processing.",
        )

    def check_transaction_status(self, transaction_reference: str) -> PaymentStatusResponse:
        """Return a fake Airtel Money transaction status response."""

        return PaymentStatusResponse(
            provider="airtel",
            rail="airtel_money",
            checkout_request_id=transaction_reference,
            provider_transaction_id=transaction_reference,
            provider_reference=transaction_reference,
            result_code="0",
            result_description="Airtel Money mock status query accepted.",
            status="query_accepted",
        )


# TODO: Add real AirtelMoneyPaymentProvider when Airtel Money API credentials and flows are scoped.
# TODO: Add MTNMoMoPaymentProvider when MTN MoMo API credentials and flows are scoped.
# TODO: Add BankTransferProvider when bank rails and reconciliation requirements are scoped.
