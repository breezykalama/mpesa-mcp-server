"""Tests for the local MCP smoke demo script."""

from __future__ import annotations

import pytest
from app.daraja.client import MockDarajaClient
from app.storage.repositories import InMemoryTransactionRepository
from scripts.smoke_mcp_tools import build_airtel_smoke_container, build_smoke_container, main


def test_smoke_mcp_tools_main_completes(capsys: pytest.CaptureFixture[str]) -> None:
    outputs = main()

    captured = capsys.readouterr()

    legacy_flow = outputs["legacy_mpesa_flow"]
    daraja_flow = outputs["generic_daraja_flow"]
    airtel_flow = outputs["generic_airtel_mock_flow"]

    assert legacy_flow["initiate_stk_push"]["status"] == "pending"
    assert legacy_flow["check_transaction_status"]["status"] == "completed"
    assert legacy_flow["simulate_callback"]["status"] == "completed"
    assert legacy_flow["generate_receipt"]["allowed"] is True
    assert legacy_flow["get_today_summary"]["allowed"] is True
    assert legacy_flow["approval_required"]["status"] == "approval_required"
    assert legacy_flow["approve_payment_request"]["status"] == "approved"
    assert legacy_flow["run_reconciliation"]["status"] == "ok"
    assert daraja_flow["provider_metadata"]["provider"] == "daraja"
    assert daraja_flow["provider_metadata"]["rail"] == "mpesa"
    assert airtel_flow["provider_metadata"]["provider"] == "airtel"
    assert airtel_flow["provider_metadata"]["rail"] == "airtel_money"
    assert '"initiate_stk_push"' in captured.out
    assert '"generic_airtel_mock_flow"' in captured.out


def test_smoke_container_uses_mock_memory_dependencies() -> None:
    container = build_smoke_container()

    assert isinstance(container.daraja_client, MockDarajaClient)
    assert isinstance(container.transaction_repository, InMemoryTransactionRepository)
    assert container.settings.daraja_mode == "mock"
    assert container.settings.storage_mode == "memory"


def test_airtel_smoke_container_uses_airtel_mock_provider() -> None:
    container = build_airtel_smoke_container()

    assert container.settings.payment_provider == "airtel_mock"
    assert container.settings.storage_mode == "memory"
