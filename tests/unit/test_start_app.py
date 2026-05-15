"""Tests for the Docker FastAPI startup script."""

from __future__ import annotations

from collections.abc import Sequence

from app.config import Settings
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
