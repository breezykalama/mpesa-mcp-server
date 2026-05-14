"""Database connection setup."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings

SessionFactory = Callable[[], Session]


def normalize_database_url(database_url: str) -> str:
    """Return a synchronous SQLAlchemy URL for repository usage."""

    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)

    return database_url


def create_database_engine(settings: Settings) -> Engine:
    """Create a SQLAlchemy engine from application settings."""

    return create_engine(normalize_database_url(settings.database_url), pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a SQLAlchemy session factory."""

    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
