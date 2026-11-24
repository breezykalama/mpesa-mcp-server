"""Shared fixtures for PostgreSQL-backed integration tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from alembic import command
from app.config import get_settings
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from tests.integration.postgres_support import alembic_config, disposable_postgres_url


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    """Reset the disposable PostgreSQL database to a freshly migrated head schema."""

    database_url = disposable_postgres_url()
    monkeypatch = pytest.MonkeyPatch()
    config = alembic_config(database_url, monkeypatch)
    try:
        command.downgrade(config, "base")
        command.upgrade(config, "head")
        yield database_url
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


@pytest.fixture(scope="module")
def engine(postgres_url: str) -> Iterator[Engine]:
    database_engine = create_engine(postgres_url)
    yield database_engine
    database_engine.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
