"""Audit repository interfaces and implementations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.storage.database import SessionFactory
from app.storage.models import AuditEventModel


class AuditEvent(BaseModel):
    """Audit event record."""

    event_id: str
    event_type: str
    payload: dict[str, Any]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    actor: str | None = None
    correlation_id: str | None = None


class AuditRepositoryProtocol(Protocol):
    """Interface for audit event persistence."""

    def save_event(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        actor: str | None = None,
        correlation_id: str | None = None,
    ) -> AuditEvent:
        """Save an audit event."""

    def list_events(self) -> list[AuditEvent]:
        """Return all audit events."""

    def list_recent_events(self, limit: int = 50) -> list[AuditEvent]:
        """Return recent audit events."""


class InMemoryAuditRepository:
    """In-memory audit repository."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def save_event(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        actor: str | None = None,
        correlation_id: str | None = None,
    ) -> AuditEvent:
        """Save an audit event."""

        event = AuditEvent(
            event_id=str(uuid4()),
            event_type=event_type,
            payload=payload,
            actor=actor,
            correlation_id=correlation_id,
        )
        self._events.append(event)
        return event

    def list_events(self) -> list[AuditEvent]:
        """Return all audit events."""

        return list(self._events)

    def list_recent_events(self, limit: int = 50) -> list[AuditEvent]:
        """Return recent audit events."""

        return sorted(
            self._events,
            key=lambda event: event.created_at,
            reverse=True,
        )[:limit]


class PostgresAuditRepository:
    """PostgreSQL-backed audit repository."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def save_event(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        actor: str | None = None,
        correlation_id: str | None = None,
    ) -> AuditEvent:
        """Save an audit event."""

        model = AuditEventModel(
            event_id=str(uuid4()),
            event_type=event_type,
            payload=payload,
            actor=actor,
            correlation_id=correlation_id,
            created_at=datetime.now(UTC),
        )
        with self._session_factory() as session:
            session.add(model)
            session.commit()
            session.refresh(model)
            return self._to_audit_event(model)

    def list_events(self) -> list[AuditEvent]:
        """Return all audit events."""

        with self._session_factory() as session:
            models = session.scalars(select(AuditEventModel)).all()
            return [self._to_audit_event(model) for model in models]

    def list_recent_events(self, limit: int = 50) -> list[AuditEvent]:
        """Return recent audit events."""

        with self._session_factory() as session:
            models = session.scalars(
                select(AuditEventModel)
                .order_by(AuditEventModel.created_at.desc())
                .limit(limit)
            ).all()
            return [self._to_audit_event(model) for model in models]

    def _to_audit_event(self, model: AuditEventModel) -> AuditEvent:
        return AuditEvent(
            event_id=model.event_id,
            event_type=model.event_type,
            payload=model.payload,
            actor=model.actor,
            correlation_id=model.correlation_id,
            created_at=model.created_at,
        )
