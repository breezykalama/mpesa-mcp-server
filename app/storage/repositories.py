"""Persistence repository interfaces and in-memory implementations."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.storage.database import SessionFactory
from app.storage.models import TransactionModel


class PendingTransaction(BaseModel):
    """Pending transaction record."""

    transaction_id: str
    idempotency_key: str | None = None
    phone_number: str
    amount: int
    account_reference: str
    description: str
    checkout_request_id: str
    merchant_request_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: str = "pending"
    result_code: int | None = None
    result_description: str | None = None
    mpesa_receipt_number: str | None = None


class TransactionRepositoryProtocol(Protocol):
    """Interface for transaction persistence."""

    def save_pending_transaction(
        self,
        *,
        phone_number: str,
        amount: int,
        account_reference: str,
        description: str,
        checkout_request_id: str,
        merchant_request_id: str,
        idempotency_key: str | None = None,
    ) -> PendingTransaction:
        """Save and return a pending transaction."""

    def get_transaction(self, transaction_id: str) -> PendingTransaction | None:
        """Return a transaction by ID if it exists."""

    def find_by_checkout_request_id(self, checkout_request_id: str) -> PendingTransaction | None:
        """Return a transaction by checkout request ID if it exists."""

    def find_by_idempotency_key(self, idempotency_key: str) -> PendingTransaction | None:
        """Return a transaction by idempotency key if it exists."""

    def update_transaction_status(
        self,
        *,
        checkout_request_id: str,
        status: str,
        result_code: int,
        result_description: str,
        mpesa_receipt_number: str | None = None,
    ) -> PendingTransaction | None:
        """Update and return a transaction status if it exists."""

    def list_transactions(self) -> list[PendingTransaction]:
        """Return all transactions."""

    def list_transactions_by_status(self, status: str) -> list[PendingTransaction]:
        """Return transactions matching a status."""

    def list_transactions_for_date(self, target_date: date) -> list[PendingTransaction]:
        """Return transactions created on a specific date."""


class InMemoryTransactionRepository:
    """In-memory transaction repository for tests and local development."""

    def __init__(self) -> None:
        self._transactions: dict[str, PendingTransaction] = {}

    def save_pending_transaction(
        self,
        *,
        phone_number: str,
        amount: int,
        account_reference: str,
        description: str,
        checkout_request_id: str,
        merchant_request_id: str,
        idempotency_key: str | None = None,
    ) -> PendingTransaction:
        """Save and return a pending transaction."""

        transaction = PendingTransaction(
            transaction_id=str(uuid4()),
            idempotency_key=idempotency_key,
            phone_number=phone_number,
            amount=amount,
            account_reference=account_reference,
            description=description,
            checkout_request_id=checkout_request_id,
            merchant_request_id=merchant_request_id,
        )
        self._transactions[transaction.transaction_id] = transaction
        return transaction

    def get_transaction(self, transaction_id: str) -> PendingTransaction | None:
        """Return a transaction by ID if it exists."""

        return self._transactions.get(transaction_id)

    def find_by_checkout_request_id(self, checkout_request_id: str) -> PendingTransaction | None:
        """Return a transaction by checkout request ID if it exists."""

        return next(
            (
                transaction
                for transaction in self._transactions.values()
                if transaction.checkout_request_id == checkout_request_id
            ),
            None,
        )

    def find_by_idempotency_key(self, idempotency_key: str) -> PendingTransaction | None:
        """Return a transaction by idempotency key if it exists."""

        return next(
            (
                transaction
                for transaction in self._transactions.values()
                if transaction.idempotency_key == idempotency_key
            ),
            None,
        )

    def update_transaction_status(
        self,
        *,
        checkout_request_id: str,
        status: str,
        result_code: int,
        result_description: str,
        mpesa_receipt_number: str | None = None,
    ) -> PendingTransaction | None:
        """Update and return a transaction status if it exists."""

        transaction = self.find_by_checkout_request_id(checkout_request_id)
        if transaction is None:
            return None

        updated_transaction = transaction.model_copy(
            update={
                "status": status,
                "result_code": result_code,
                "result_description": result_description,
                "mpesa_receipt_number": mpesa_receipt_number,
                "updated_at": datetime.now(UTC),
            }
        )
        self._transactions[updated_transaction.transaction_id] = updated_transaction
        return updated_transaction

    def list_transactions(self) -> list[PendingTransaction]:
        """Return all transactions."""

        return list(self._transactions.values())

    def list_transactions_by_status(self, status: str) -> list[PendingTransaction]:
        """Return transactions matching a status."""

        return [
            transaction
            for transaction in self._transactions.values()
            if transaction.status == status
        ]

    def list_transactions_for_date(self, target_date: date) -> list[PendingTransaction]:
        """Return transactions created on a specific date."""

        return [
            transaction
            for transaction in self._transactions.values()
            if transaction.created_at.date() == target_date
        ]


class PostgresTransactionRepository:
    """PostgreSQL-backed transaction repository."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def save_pending_transaction(
        self,
        *,
        phone_number: str,
        amount: int,
        account_reference: str,
        description: str,
        checkout_request_id: str,
        merchant_request_id: str,
        idempotency_key: str | None = None,
    ) -> PendingTransaction:
        """Save and return a pending transaction."""

        now = datetime.now(UTC)
        model = TransactionModel(
            transaction_id=str(uuid4()),
            idempotency_key=idempotency_key,
            phone_number=phone_number,
            amount=amount,
            account_reference=account_reference,
            description=description,
            checkout_request_id=checkout_request_id,
            merchant_request_id=merchant_request_id,
            status="pending",
            created_at=now,
            updated_at=now,
        )

        with self._session_factory() as session:
            session.add(model)
            session.commit()
            session.refresh(model)
            return self._to_pending_transaction(model)

    def get_transaction(self, transaction_id: str) -> PendingTransaction | None:
        """Return a transaction by ID if it exists."""

        with self._session_factory() as session:
            model = session.get(TransactionModel, transaction_id)
            return self._to_pending_transaction(model) if model is not None else None

    def find_by_checkout_request_id(self, checkout_request_id: str) -> PendingTransaction | None:
        """Return a transaction by checkout request ID if it exists."""

        with self._session_factory() as session:
            model = session.scalar(
                select(TransactionModel).where(
                    TransactionModel.checkout_request_id == checkout_request_id
                )
            )
            return self._to_pending_transaction(model) if model is not None else None

    def find_by_idempotency_key(self, idempotency_key: str) -> PendingTransaction | None:
        """Return a transaction by idempotency key if it exists."""

        with self._session_factory() as session:
            model = session.scalar(
                select(TransactionModel).where(TransactionModel.idempotency_key == idempotency_key)
            )
            return self._to_pending_transaction(model) if model is not None else None

    def update_transaction_status(
        self,
        *,
        checkout_request_id: str,
        status: str,
        result_code: int,
        result_description: str,
        mpesa_receipt_number: str | None = None,
    ) -> PendingTransaction | None:
        """Update and return a transaction status if it exists."""

        with self._session_factory() as session:
            model = session.scalar(
                select(TransactionModel).where(
                    TransactionModel.checkout_request_id == checkout_request_id
                )
            )
            if model is None:
                return None

            model.status = status
            model.result_code = result_code
            model.result_description = result_description
            model.mpesa_receipt_number = mpesa_receipt_number
            model.updated_at = datetime.now(UTC)
            session.commit()
            session.refresh(model)
            return self._to_pending_transaction(model)

    def list_transactions(self) -> list[PendingTransaction]:
        """Return all transactions."""

        with self._session_factory() as session:
            models = session.scalars(select(TransactionModel)).all()
            return [self._to_pending_transaction(model) for model in models]

    def list_transactions_by_status(self, status: str) -> list[PendingTransaction]:
        """Return transactions matching a status."""

        with self._session_factory() as session:
            models = session.scalars(
                select(TransactionModel).where(TransactionModel.status == status)
            ).all()
            return [self._to_pending_transaction(model) for model in models]

    def list_transactions_for_date(self, target_date: date) -> list[PendingTransaction]:
        """Return transactions created on a specific date."""

        start = datetime.combine(target_date, datetime.min.time(), tzinfo=UTC)
        end = datetime.combine(target_date, datetime.max.time(), tzinfo=UTC)

        with self._session_factory() as session:
            models = session.scalars(
                select(TransactionModel).where(
                    TransactionModel.created_at >= start,
                    TransactionModel.created_at <= end,
                )
            ).all()
            return [self._to_pending_transaction(model) for model in models]

    def _to_pending_transaction(self, model: TransactionModel) -> PendingTransaction:
        return PendingTransaction(
            transaction_id=model.transaction_id,
            idempotency_key=model.idempotency_key,
            phone_number=model.phone_number,
            amount=model.amount,
            account_reference=model.account_reference,
            description=model.description,
            checkout_request_id=model.checkout_request_id,
            merchant_request_id=model.merchant_request_id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            status=model.status,
            result_code=model.result_code,
            result_description=model.result_description,
            mpesa_receipt_number=model.mpesa_receipt_number,
        )
