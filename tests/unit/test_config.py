"""Tests for application settings."""

from app.config import Settings
from pytest import MonkeyPatch


def test_default_max_stk_amount() -> None:
    settings = Settings(database_url="postgresql+asyncpg://user:pass@localhost:5432/test")

    assert settings.max_stk_amount == 10_000
    assert settings.reconciliation_stale_pending_minutes == 15


def test_env_override_for_max_stk_amount(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_STK_AMOUNT", "25000")

    settings = Settings(database_url="postgresql+asyncpg://user:pass@localhost:5432/test")

    assert settings.max_stk_amount == 25_000
