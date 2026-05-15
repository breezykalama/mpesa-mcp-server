"""Start the FastAPI app, applying database migrations when needed."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import Settings, get_settings  # noqa: E402
from app.logging.config import configure_logging  # noqa: E402

logger = logging.getLogger(__name__)

CommandRunner = Callable[[Sequence[str]], None]


def run_migrations_if_needed(
    settings: Settings,
    *,
    command_runner: CommandRunner | None = None,
) -> None:
    """Run Alembic migrations when Postgres storage is configured."""

    if settings.storage_mode != "postgres":
        logger.info(
            "Skipping database migrations.",
            extra={"event_type": "database_migration_skipped"},
        )
        return

    runner = command_runner or run_command
    logger.info(
        "Running database migrations.",
        extra={"event_type": "database_migration_started"},
    )
    try:
        runner(("alembic", "upgrade", "head"))
    except Exception:
        logger.exception(
            "Database migration failed.",
            extra={"event_type": "database_migration_failed", "status": "failed"},
        )
        raise

    logger.info(
        "Database migrations completed.",
        extra={"event_type": "database_migration_completed"},
    )


def start_uvicorn(settings: Settings) -> None:
    """Start the FastAPI app with Uvicorn."""

    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    logger.info(
        "Starting FastAPI app.",
        extra={"event_type": "fastapi_starting"},
    )
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        log_config=None,
        log_level=settings.log_level.lower(),
    )


def run_command(command: Sequence[str]) -> None:
    """Run a subprocess command and fail fast on errors."""

    subprocess.run(command, check=True)


def main() -> None:
    """Apply startup tasks and run FastAPI."""

    settings = get_settings()
    configure_logging(log_level=settings.log_level, log_format=settings.log_format)
    run_migrations_if_needed(settings)
    start_uvicorn(settings)


if __name__ == "__main__":
    main()
