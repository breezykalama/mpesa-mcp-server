"""Tests for audit repositories and logger."""

from __future__ import annotations

from app.audit.logger import InMemoryAuditLogger
from app.audit.repository import InMemoryAuditRepository, PostgresAuditRepository
from app.bootstrap.container import AppContainer
from app.observability.tracing import correlation_context
from app.storage.models import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def build_postgres_audit_repository() -> PostgresAuditRepository:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return PostgresAuditRepository(
        sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    )


def test_audit_event_saved() -> None:
    repository = InMemoryAuditRepository()

    event = repository.save_event(event_type="payment.started", payload={"amount": 1_000})

    assert event.event_id
    assert event.event_type == "payment.started"
    assert repository.list_events() == [event]


def test_audit_event_includes_timestamp() -> None:
    repository = InMemoryAuditRepository()

    event = repository.save_event(event_type="payment.started", payload={})

    assert event.created_at is not None


def test_correlation_id_can_be_stored() -> None:
    repository = InMemoryAuditRepository()

    event = repository.save_event(
        event_type="payment.started",
        payload={},
        actor="agent",
        correlation_id="corr-123",
    )

    assert event.actor == "agent"
    assert event.correlation_id == "corr-123"


def test_memory_mode_uses_in_memory_audit_repository() -> None:
    container = AppContainer.mock()

    assert isinstance(container.audit_repository, InMemoryAuditRepository)
    container.audit_logger.log_event("payment.started", {"amount": 1_000})
    assert len(container.audit_logger.events) == 1


def test_audit_logger_uses_repository() -> None:
    repository = InMemoryAuditRepository()
    logger = InMemoryAuditLogger(repository=repository)

    logger.log_event(
        "payment.started",
        {"amount": 1_000},
        actor="agent",
        correlation_id="corr-456",
    )

    events = repository.list_events()
    assert len(events) == 1
    assert events[0].actor == "agent"
    assert events[0].correlation_id == "corr-456"


def test_audit_logger_uses_context_correlation_id() -> None:
    repository = InMemoryAuditRepository()
    logger = InMemoryAuditLogger(repository=repository)

    with correlation_context("corr-from-context"):
        logger.log_event("payment.started", {"amount": 1_000})

    events = repository.list_events()
    assert len(events) == 1
    assert events[0].correlation_id == "corr-from-context"


def test_postgres_audit_repository_saves_event_with_sqlite_safe_pattern() -> None:
    repository = build_postgres_audit_repository()

    event = repository.save_event(
        event_type="payment.started",
        payload={"amount": 1_000},
        actor="agent",
        correlation_id="corr-789",
    )

    events = repository.list_events()
    assert len(events) == 1
    assert events[0].event_id == event.event_id
    assert events[0].payload == {"amount": 1_000}
    assert events[0].actor == "agent"
    assert events[0].correlation_id == "corr-789"
