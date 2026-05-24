"""Tests for infrastructure dependency health policy."""

from __future__ import annotations

import pytest
from app.config import Settings
from app.infrastructure import health
from app.infrastructure.health import (
    DependencyProbeResult,
    InfrastructureUnavailableError,
    validate_startup_dependencies,
)


def test_startup_fails_when_postgres_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        health,
        "check_postgres",
        lambda settings: DependencyProbeResult("postgres", False, "connection failed"),
    )
    settings = Settings(
        storage_mode="postgres",
        database_url="postgresql+psycopg://user:pass@localhost:5432/db",
        operator_auth_enabled=False,
    )

    with pytest.raises(InfrastructureUnavailableError) as exc_info:
        validate_startup_dependencies(settings)

    assert "postgres" in str(exc_info.value)


def test_startup_fails_when_redis_unavailable_for_redis_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        health,
        "check_redis",
        lambda settings: DependencyProbeResult("redis", False, "connection failed"),
    )
    settings = Settings(
        database_url="postgresql+asyncpg://user:pass@localhost:5432/db",
        rate_limit_mode="redis",
        callback_replay_mode="redis",
        operator_auth_enabled=False,
    )

    with pytest.raises(InfrastructureUnavailableError) as exc_info:
        validate_startup_dependencies(settings)

    assert "redis" in str(exc_info.value)


def test_startup_dependencies_pass_for_memory_modes() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://user:pass@localhost:5432/db",
        storage_mode="memory",
        rate_limit_mode="memory",
        callback_replay_mode="memory",
        operator_auth_enabled=False,
    )

    validate_startup_dependencies(settings)

