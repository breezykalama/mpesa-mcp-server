"""Tests for the local MCP smoke demo script."""

from __future__ import annotations

import pytest
from app.daraja.client import MockDarajaClient
from app.storage.repositories import InMemoryTransactionRepository
from scripts.smoke_mcp_tools import build_smoke_container, main


def test_smoke_mcp_tools_main_completes(capsys: pytest.CaptureFixture[str]) -> None:
    outputs = main()

    captured = capsys.readouterr()

    assert outputs["initiate_stk_push"]["status"] == "pending"
    assert outputs["check_transaction_status"]["status"] == "completed"
    assert outputs["simulate_callback"]["status"] == "completed"
    assert outputs["generate_receipt"]["allowed"] is True
    assert outputs["get_today_summary"]["allowed"] is True
    assert outputs["approval_required"]["status"] == "approval_required"
    assert outputs["approve_payment_request"]["status"] == "approved"
    assert '"initiate_stk_push"' in captured.out


def test_smoke_container_uses_mock_memory_dependencies() -> None:
    container = build_smoke_container()

    assert isinstance(container.daraja_client, MockDarajaClient)
    assert isinstance(container.transaction_repository, InMemoryTransactionRepository)
    assert container.settings.daraja_mode == "mock"
    assert container.settings.storage_mode == "memory"
