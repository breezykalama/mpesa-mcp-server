"""Tests for application dependency container."""

from __future__ import annotations

from app.audit.repository import InMemoryAuditRepository, PostgresAuditRepository
from app.bootstrap.container import DEFAULT_MOCK_DATABASE_URL, AppContainer
from app.callbacks.replay import InMemoryReplayProtection, RedisReplayProtection
from app.config import Settings
from app.daraja.client import MockDarajaClient, RealDarajaClient
from app.mcp.server import create_mcp_server, create_tool_handlers, list_registered_tool_names
from app.policy.tool_policy import ToolPolicyEngine
from app.rate_limit.limiter import InMemoryRateLimiter, RedisRateLimiter
from app.services.payment_service import PaymentService
from app.services.receipt_service import ReceiptService
from app.services.transaction_service import TransactionService
from app.storage.repositories import InMemoryTransactionRepository, PostgresTransactionRepository


def test_container_builds_successfully() -> None:
    container = AppContainer.mock()

    assert container.settings.database_url == DEFAULT_MOCK_DATABASE_URL
    assert isinstance(container.daraja_client, MockDarajaClient)
    assert isinstance(container.transaction_repository, InMemoryTransactionRepository)
    assert isinstance(container.audit_repository, InMemoryAuditRepository)
    assert isinstance(container.tool_policy, ToolPolicyEngine)
    assert isinstance(container.rate_limiter, InMemoryRateLimiter)
    assert isinstance(container.replay_protection, InMemoryReplayProtection)


def test_services_are_correctly_wired() -> None:
    container = AppContainer.mock(settings=Settings(database_url="sqlite://", max_stk_amount=500))

    assert isinstance(container.payment_service, PaymentService)
    assert isinstance(container.transaction_service, TransactionService)
    assert isinstance(container.receipt_service, ReceiptService)

    response = container.payment_service.initiate_stk_push(
        phone_number="254700000000",
        amount=501,
        account_reference="INV-001",
        description="Invoice payment",
    )

    assert response.status == "approval_required"
    assert response.requires_approval is True


def test_mcp_server_uses_container() -> None:
    container = AppContainer.mock()

    server = create_mcp_server(container=container)
    handlers = create_tool_handlers(container)
    response = handlers["initiate_stk_push"](
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-001",
        description="Invoice payment",
    )

    assert response["status"] == "pending"
    assert set(list_registered_tool_names(server)) == {
        "initiate_stk_push",
        "check_transaction_status",
        "generate_receipt",
        "get_today_summary",
        "get_failed_transactions",
        "approve_payment_request",
        "reject_payment_request",
    }
    assert container.transaction_repository.list_transactions() != []


def test_container_selects_real_daraja_client_for_sandbox_mode() -> None:
    container = AppContainer.mock(
        settings=Settings(
            database_url="postgresql+asyncpg://user:pass@localhost:5432/test",
            daraja_mode="sandbox",
            daraja_consumer_key="consumer-key",
            daraja_consumer_secret="consumer-secret",
            daraja_passkey="passkey",
            daraja_shortcode="174379",
            daraja_callback_url="https://example.test/callback",
        )
    )

    assert isinstance(container.daraja_client, RealDarajaClient)


def test_container_selects_postgres_repository_for_postgres_storage_mode() -> None:
    container = AppContainer.mock(
        settings=Settings(
            database_url="sqlite+pysqlite:///:memory:",
            storage_mode="postgres",
        )
    )

    assert isinstance(container.transaction_repository, PostgresTransactionRepository)
    assert isinstance(container.audit_repository, PostgresAuditRepository)


def test_container_selects_redis_rate_limiter_for_redis_mode() -> None:
    container = AppContainer.mock(
        settings=Settings(
            database_url="postgresql+asyncpg://mpesa:mpesa@localhost:5432/mpesa_mcp",
            rate_limit_mode="redis",
            redis_url="redis://localhost:6379/1",
        )
    )

    assert isinstance(container.rate_limiter, RedisRateLimiter)


def test_container_selects_redis_replay_protection_for_redis_mode() -> None:
    container = AppContainer.mock(
        settings=Settings(
            database_url="postgresql+asyncpg://mpesa:mpesa@localhost:5432/mpesa_mcp",
            callback_replay_mode="redis",
            redis_url="redis://localhost:6379/1",
        )
    )

    assert isinstance(container.replay_protection, RedisReplayProtection)
