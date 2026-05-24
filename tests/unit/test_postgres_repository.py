"""Tests for SQLAlchemy-backed transaction repository."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.config import get_settings
from app.storage.models import Base, TransactionModel
from app.storage.repositories import (
    DuplicateTransactionIntegrityError,
    PostgresTransactionRepository,
)
from app.transactions.state_machine import TransactionStateTransitionError
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def build_repository() -> PostgresTransactionRepository:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return PostgresTransactionRepository(
        sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    )


def test_postgres_repository_saves_and_finds_pending_transaction() -> None:
    repository = build_repository()

    transaction = repository.save_pending_transaction(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-001",
        description="Invoice payment",
        checkout_request_id="ws_CO_123",
        merchant_request_id="mock_123",
        idempotency_key="idem-123",
    )

    found = repository.find_by_checkout_request_id("ws_CO_123")
    found_by_idempotency_key = repository.find_by_idempotency_key("idem-123")

    assert found is not None
    assert found.transaction_id == transaction.transaction_id
    assert found.status == "pending"
    assert found.provider == "daraja"
    assert found.rail == "mpesa"
    assert found_by_idempotency_key is not None
    assert found_by_idempotency_key.transaction_id == transaction.transaction_id


def test_postgres_repository_rejects_duplicate_idempotency_key() -> None:
    repository = build_repository()
    repository.save_pending_transaction(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-001",
        description="Invoice payment",
        checkout_request_id="ws_CO_123",
        merchant_request_id="mock_123",
        idempotency_key="idem-duplicate",
    )

    with pytest.raises(DuplicateTransactionIntegrityError):
        repository.save_pending_transaction(
            phone_number="254700000001",
            amount=1_500,
            account_reference="INV-002",
            description="Invoice payment",
            checkout_request_id="ws_CO_456",
            merchant_request_id="mock_456",
            idempotency_key="idem-duplicate",
        )


def test_postgres_repository_stores_provider_fields() -> None:
    repository = build_repository()

    transaction = repository.save_pending_transaction(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-001",
        description="Invoice payment",
        checkout_request_id="ws_CO_PROVIDER",
        merchant_request_id="mock_provider",
        provider="daraja",
        rail="mpesa",
        provider_transaction_id="ws_CO_PROVIDER",
        provider_reference="mock_provider",
    )

    found = repository.get_transaction(transaction.transaction_id)

    assert found is not None
    assert found.provider == "daraja"
    assert found.rail == "mpesa"
    assert found.provider_transaction_id == "ws_CO_PROVIDER"
    assert found.provider_reference == "mock_provider"


def test_postgres_repository_rejects_duplicate_provider_transaction_id() -> None:
    repository = build_repository()
    repository.save_pending_transaction(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-001",
        description="Invoice payment",
        checkout_request_id="ws_CO_PROVIDER_1",
        merchant_request_id="mock_provider_1",
        provider_transaction_id="provider-txn-duplicate",
    )

    with pytest.raises(DuplicateTransactionIntegrityError):
        repository.save_pending_transaction(
            phone_number="254700000001",
            amount=1_500,
            account_reference="INV-002",
            description="Invoice payment",
            checkout_request_id="ws_CO_PROVIDER_2",
            merchant_request_id="mock_provider_2",
            provider_transaction_id="provider-txn-duplicate",
        )


def test_postgres_repository_updates_transaction_status() -> None:
    repository = build_repository()
    repository.save_pending_transaction(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-001",
        description="Invoice payment",
        checkout_request_id="ws_CO_123",
        merchant_request_id="mock_123",
    )

    updated = repository.update_transaction_status(
        checkout_request_id="ws_CO_123",
        status="completed",
        result_code=0,
        result_description="Success",
        mpesa_receipt_number="RCP123",
    )

    assert updated is not None
    assert updated.status == "completed"
    assert updated.result_code == 0
    assert updated.mpesa_receipt_number == "RCP123"


def test_postgres_repository_does_not_mutate_on_invalid_transition() -> None:
    repository = build_repository()
    repository.save_pending_transaction(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-001",
        description="Invoice payment",
        checkout_request_id="ws_CO_INVALID",
        merchant_request_id="mock_invalid",
    )
    repository.update_transaction_status(
        checkout_request_id="ws_CO_INVALID",
        status="completed",
        result_code=0,
        result_description="Success",
        mpesa_receipt_number="RCP123",
    )

    try:
        repository.update_transaction_status(
            checkout_request_id="ws_CO_INVALID",
            status="failed",
            result_code=1032,
            result_description="Late failure",
            mpesa_receipt_number=None,
        )
    except TransactionStateTransitionError:
        pass
    else:
        raise AssertionError("Expected invalid transition to raise.")

    transaction = repository.find_by_checkout_request_id("ws_CO_INVALID")

    assert transaction is not None
    assert transaction.status == "completed"
    assert transaction.result_description == "Success"
    assert transaction.mpesa_receipt_number == "RCP123"


def test_transaction_status_check_constraint_rejects_invalid_status() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with session_factory() as session:
        session.add(
            TransactionModel(
                transaction_id="txn-invalid-status",
                phone_number="254700000000",
                amount=1_000,
                account_reference="INV-INVALID",
                description="Invoice payment",
                checkout_request_id="ws_CO_INVALID_STATUS",
                merchant_request_id="mock_invalid_status",
                status="unknown",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )

        with pytest.raises(IntegrityError):
            session.commit()


def test_alembic_migrations_apply_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "migration-test.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{database_path}")
    get_settings.cache_clear()
    config = Config("alembic.ini")

    try:
        command.upgrade(config, "head")
    finally:
        get_settings.cache_clear()


def test_postgres_repository_lists_by_status_and_date() -> None:
    repository = build_repository()
    repository.save_pending_transaction(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-001",
        description="Invoice payment",
        checkout_request_id="ws_CO_123",
        merchant_request_id="mock_123",
    )
    repository.update_transaction_status(
        checkout_request_id="ws_CO_123",
        status="failed",
        result_code=1032,
        result_description="Cancelled",
    )

    failed = repository.list_transactions_by_status("failed")
    today = repository.list_transactions_for_date(datetime.now(UTC).date())

    assert len(failed) == 1
    assert failed[0].checkout_request_id == "ws_CO_123"
    assert len(today) == 1
