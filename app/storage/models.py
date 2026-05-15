"""Persistence models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for persistence models."""


class TransactionModel(Base):
    """Transaction persistence model."""

    __tablename__ = "transactions"

    transaction_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(64), default="daraja", index=True)
    rail: Mapped[str] = mapped_column(String(64), default="mpesa", index=True)
    provider_transaction_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    provider_reference: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    checkout_request_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    merchant_request_id: Mapped[str] = mapped_column(String(128), index=True)
    phone_number: Mapped[str] = mapped_column(String(32))
    amount: Mapped[int] = mapped_column(Integer)
    account_reference: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), index=True, default="pending")
    result_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mpesa_receipt_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class AuditEventModel(Base):
    """Audit event persistence model."""

    __tablename__ = "audit_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        index=True,
    )
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
