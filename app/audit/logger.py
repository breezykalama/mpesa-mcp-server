"""Audit logger interfaces and implementations."""

from __future__ import annotations

from typing import Any, Protocol

from app.audit.repository import AuditEvent, AuditRepositoryProtocol, InMemoryAuditRepository


class AuditLoggerProtocol(Protocol):
    """Interface for audit logging."""

    def log_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        actor: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Log an audit event."""


class InMemoryAuditLogger:
    """Audit logger backed by a swappable repository."""

    def __init__(self, repository: AuditRepositoryProtocol | None = None) -> None:
        self._repository = repository or InMemoryAuditRepository()

    @property
    def events(self) -> list[AuditEvent]:
        """Return stored audit events."""

        return self._repository.list_events()

    def log_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        actor: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Log an audit event."""

        self._repository.save_event(
            event_type=event_type,
            payload=payload,
            actor=actor,
            correlation_id=correlation_id,
        )
