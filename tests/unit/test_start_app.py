"""Tests for the Docker FastAPI startup script."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from app.config import Settings
from scripts import start_app
from scripts.start_app import run_migrations_if_needed


def test_postgres_mode_runs_migration() -> None:
    commands: list[Sequence[str]] = []
    settings = Settings(
        database_url="postgresql+psycopg://mpesa:mpesa@localhost:5432/mpesa_mcp",
        storage_mode="postgres",
    )

    run_migrations_if_needed(settings, command_runner=commands.append)

    assert commands == [("alembic", "upgrade", "head")]


def test_memory_mode_skips_migration() -> None:
    commands: list[Sequence[str]] = []
    settings = Settings(
        database_url="postgresql+psycopg://mpesa:mpesa@localhost:5432/mpesa_mcp",
        storage_mode="memory",
    )

    run_migrations_if_needed(settings, command_runner=commands.append)

    assert commands == []


def test_main_validates_startup_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    settings = Settings(
        database_url="postgresql+psycopg://mpesa:mpesa@localhost:5432/mpesa_mcp",
        storage_mode="memory",
        operator_auth_enabled=False,
    )

    monkeypatch.setattr(start_app, "get_settings", lambda: settings)
    monkeypatch.setattr(start_app, "configure_logging", lambda **kwargs: None)
    monkeypatch.setattr(start_app, "start_uvicorn", lambda settings: None)
    monkeypatch.setattr(
        start_app,
        "enforce_startup_safety",
        lambda settings: calls.append("startup_safety_enforced"),
    )
    monkeypatch.setattr(
        start_app,
        "run_migrations_if_needed",
        lambda settings: calls.append("migrations"),
    )

    start_app.main()

    assert calls == ["startup_safety_enforced", "migrations"]
