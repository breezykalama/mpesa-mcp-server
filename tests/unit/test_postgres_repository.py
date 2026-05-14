"""Tests for SQLAlchemy-backed transaction repository."""

from __future__ import annotations

from datetime import UTC, datetime

from app.storage.models import Base
from app.storage.repositories import PostgresTransactionRepository
from sqlalchemy import create_engine
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
    assert found_by_idempotency_key is not None
    assert found_by_idempotency_key.transaction_id == transaction.transaction_id


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
