"""Run a local smoke demo against MCP tool wrappers in mock mode."""

# ruff: noqa: E402, I001

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.bootstrap.container import AppContainer, DEFAULT_MOCK_DATABASE_URL
from app.callbacks.handlers import StkCallbackHandler
from app.config import Settings
from app.daraja.client import MockDarajaClient
from app.mcp.schemas import McpToolResponse
from app.mcp.tools import (
    approve_payment_request_tool,
    check_payment_status_tool,
    check_transaction_status_tool,
    generate_receipt_tool,
    get_today_summary_tool,
    initiate_payment_tool,
    initiate_stk_push_tool,
    run_reconciliation_tool,
)


def build_smoke_container() -> AppContainer:
    """Build a container that cannot make live Daraja or database calls."""

    return AppContainer.mock(
        settings=Settings(
            database_url=DEFAULT_MOCK_DATABASE_URL,
            storage_mode="memory",
            daraja_mode="mock",
            rate_limit_enabled=False,
            rate_limit_mode="memory",
            max_stk_amount=10_000,
        )
    )


def build_airtel_smoke_container() -> AppContainer:
    """Build an Airtel mock-backed container with in-memory storage."""

    return AppContainer.mock(
        settings=Settings(
            database_url=DEFAULT_MOCK_DATABASE_URL,
            storage_mode="memory",
            payment_provider="airtel_mock",
            daraja_mode="mock",
            rate_limit_enabled=False,
            rate_limit_mode="memory",
            max_stk_amount=10_000,
        )
    )


def run_smoke_demo() -> dict[str, Any]:
    """Execute the local MCP smoke flow and return serializable outputs."""

    daraja_container = build_smoke_container()
    if not isinstance(daraja_container.daraja_client, MockDarajaClient):
        raise RuntimeError("Smoke demo must run with MockDarajaClient only.")

    airtel_container = build_airtel_smoke_container()

    return {
        "legacy_mpesa_flow": run_legacy_mpesa_flow(daraja_container),
        "generic_daraja_flow": run_generic_payment_flow(
            daraja_container,
            idempotency_key="smoke-generic-daraja-payment-001",
            account_reference="INV-SMOKE-GENERIC-DARAJA-001",
            description="Smoke demo generic Daraja payment",
        ),
        "generic_airtel_mock_flow": run_generic_payment_flow(
            airtel_container,
            idempotency_key="smoke-generic-airtel-payment-001",
            account_reference="INV-SMOKE-GENERIC-AIRTEL-001",
            description="Smoke demo generic Airtel mock payment",
        ),
    }


def run_legacy_mpesa_flow(container: AppContainer) -> dict[str, Any]:
    """Run the legacy M-Pesa-first compatibility flow."""

    outputs: dict[str, Any] = {}
    initiate_response = initiate_stk_push_tool(
        {
            "phone_number": "254700000000",
            "amount": 1000,
            "account_reference": "INV-SMOKE-001",
            "description": "Smoke demo invoice payment",
            "idempotency_key": "smoke-safe-payment-001",
        },
        container.payment_service,
    )
    outputs["initiate_stk_push"] = _tool_response(initiate_response)

    checkout_request_id = _required_data_value(
        initiate_response,
        "checkout_request_id",
    )

    status_response = check_transaction_status_tool(
        {"checkout_request_id": checkout_request_id},
        container.transaction_service,
    )
    outputs["check_transaction_status"] = _tool_response(status_response)

    callback_handler = StkCallbackHandler(
        transaction_repository=container.transaction_repository,
        audit_logger=container.audit_logger,
        metrics_recorder=container.metrics_recorder,
    )
    callback_result = callback_handler.process(
        {
            "Body": {
                "stkCallback": {
                    "CheckoutRequestID": checkout_request_id,
                    "ResultCode": 0,
                    "ResultDesc": "The service request is processed successfully.",
                    "CallbackMetadata": {
                        "Item": [
                            {"Name": "Amount", "Value": 1000},
                            {"Name": "MpesaReceiptNumber", "Value": "SMOKE12345"},
                            {"Name": "PhoneNumber", "Value": "254700000000"},
                        ]
                    },
                }
            }
        }
    )
    outputs["simulate_callback"] = callback_result.model_dump(
        mode="json",
        exclude_none=True,
    )

    receipt_response = generate_receipt_tool(
        {"checkout_request_id": checkout_request_id},
        container.receipt_service,
    )
    outputs["generate_receipt"] = _tool_response(receipt_response)

    today_summary_response = get_today_summary_tool({}, container.analytics_service)
    outputs["get_today_summary"] = _tool_response(today_summary_response)

    approval_response = initiate_stk_push_tool(
        {
            "phone_number": "254700000000",
            "amount": container.settings.max_stk_amount + 1,
            "account_reference": "INV-SMOKE-APPROVAL-001",
            "description": "Smoke demo approval payment",
            "idempotency_key": "smoke-approval-payment-001",
        },
        container.payment_service,
    )
    outputs["approval_required"] = _tool_response(approval_response)

    approval_id = _required_data_value(approval_response, "approval_id")
    approval_execution_response = approve_payment_request_tool(
        {"approval_id": approval_id},
        container.payment_service,
    )
    outputs["approve_payment_request"] = _tool_response(approval_execution_response)

    reconciliation_response = run_reconciliation_tool({}, container.reconciliation_service)
    outputs["run_reconciliation"] = _tool_response(reconciliation_response)

    return outputs


def run_generic_payment_flow(
    container: AppContainer,
    *,
    idempotency_key: str,
    account_reference: str,
    description: str,
) -> dict[str, Any]:
    """Run a generic provider-agnostic payment flow."""

    outputs: dict[str, Any] = {}
    initiate_response = initiate_payment_tool(
        {
            "phone_number": "254700000000",
            "amount": 1000,
            "account_reference": account_reference,
            "description": description,
            "idempotency_key": idempotency_key,
        },
        container.payment_service,
    )
    outputs["initiate_payment"] = _tool_response(initiate_response)

    provider_transaction_id = _required_data_value(
        initiate_response,
        "checkout_request_id",
    )
    status_response = check_payment_status_tool(
        {"provider_transaction_id": provider_transaction_id},
        container.transaction_service,
    )
    outputs["check_payment_status"] = _tool_response(status_response)
    outputs["provider_metadata"] = _transaction_provider_metadata(
        container,
        _required_data_value(initiate_response, "transaction_id"),
    )

    reconciliation_response = run_reconciliation_tool({}, container.reconciliation_service)
    outputs["run_reconciliation"] = _tool_response(reconciliation_response)
    return outputs


def main() -> dict[str, Any]:
    """Run the smoke demo and print clean JSON."""

    outputs = run_smoke_demo()
    print(json.dumps(outputs, indent=2))
    return outputs


def _tool_response(response: McpToolResponse) -> dict[str, Any]:
    return response.model_dump(mode="json", exclude_none=True)


def _required_data_value(response: McpToolResponse, key: str) -> str:
    value = response.data.get(key)
    if not isinstance(value, str) or value == "":
        raise RuntimeError(f"Expected {key} in {response.status} response.")
    return value


def _transaction_provider_metadata(
    container: AppContainer,
    transaction_id: str,
) -> dict[str, str | None]:
    transaction = container.transaction_repository.get_transaction(transaction_id)
    if transaction is None:
        raise RuntimeError(f"Expected transaction {transaction_id} to exist.")

    return {
        "provider": transaction.provider,
        "rail": transaction.rail,
        "provider_transaction_id": transaction.provider_transaction_id,
        "provider_reference": transaction.provider_reference,
    }


if __name__ == "__main__":
    main()
