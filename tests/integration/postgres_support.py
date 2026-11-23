"""Helpers for integration tests that need a disposable PostgreSQL database.

PostgreSQL tests run only when ``MPESA_TEST_POSTGRES_URL`` points at a local database
whose name ends in ``_test``; otherwise they skip. A misconfigured URL fails loudly
rather than risk touching a shared or production database.
"""

from __future__ import annotations

import os

import pytest
from alembic.config import Config
from app.config import get_settings
from sqlalchemy.engine import make_url

POSTGRES_URL_ENV = "MPESA_TEST_POSTGRES_URL"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def alembic_config(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Config:
    """Return an Alembic config for in-process migrations.

    The config is built without ``alembic.ini`` as its file so that ``alembic/env.py``
    skips ``logging.config.fileConfig``, which would otherwise disable the application's
    existing loggers and break log assertions in later tests.
    """

    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config()
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def disposable_postgres_url() -> str:
    """Return the guarded disposable PostgreSQL URL, or skip when it is not configured."""

    raw_url = os.environ.get(POSTGRES_URL_ENV, "").strip()
    if not raw_url:
        # CI greps for this message to fail the build if PostgreSQL tests are skipped.
        pytest.skip(f"{POSTGRES_URL_ENV} is not set; PostgreSQL schema tests skipped")

    url = make_url(raw_url)
    if not url.drivername.startswith("postgresql"):
        pytest.fail(f"{POSTGRES_URL_ENV} must be a PostgreSQL URL")
    if url.host not in LOCAL_HOSTS:
        pytest.fail(f"{POSTGRES_URL_ENV} must point at a local disposable instance")
    if not (url.database or "").endswith("_test"):
        pytest.fail(f"{POSTGRES_URL_ENV} database name must end with '_test'")
    return raw_url
