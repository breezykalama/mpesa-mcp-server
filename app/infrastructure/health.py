"""Infrastructure dependency health probes.

Dependency failure policy:
- Postgres storage fails startup when unavailable. Runtime DB errors are converted
  into domain-level infrastructure errors instead of leaking database stack traces.
- Redis-backed safety controls fail startup when unavailable. Runtime Redis failures
  fail closed: rate limiting rejects sensitive tool calls and callback replay
  protection rejects callbacks.
- Daraja availability is handled by the Daraja client timeout, retry, and circuit
  breaker behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast

from redis import Redis
from sqlalchemy import text

from app.config import Settings
from app.storage.database import create_database_engine


class InfrastructureUnavailableError(RuntimeError):
    """Raised when a required infrastructure dependency is unavailable."""


@dataclass(frozen=True)
class DependencyProbeResult:
    """Structured dependency probe result."""

    name: str
    ok: bool
    reason: str


class RedisPingClientProtocol(Protocol):
    """Small Redis ping client contract."""

    def ping(self) -> bool:
        """Return whether Redis responds to ping."""


def check_postgres(settings: Settings) -> DependencyProbeResult:
    """Check PostgreSQL connectivity for postgres storage mode."""

    if settings.storage_mode != "postgres":
        return DependencyProbeResult("postgres", True, "not_configured")

    try:
        engine = create_database_engine(settings)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()
    except Exception as exc:
        return DependencyProbeResult("postgres", False, _safe_reason(exc))

    return DependencyProbeResult("postgres", True, "available")


def check_redis(settings: Settings) -> DependencyProbeResult:
    """Check Redis connectivity when Redis-backed controls are configured."""

    if not _redis_required(settings):
        return DependencyProbeResult("redis", True, "not_configured")

    try:
        client = cast(
            RedisPingClientProtocol,
            Redis.from_url(settings.redis_url, decode_responses=True),
        )
        client.ping()
    except Exception as exc:
        return DependencyProbeResult("redis", False, _safe_reason(exc))

    return DependencyProbeResult("redis", True, "available")


def readiness_dependency_results(settings: Settings) -> list[DependencyProbeResult]:
    """Return dependency readiness results for configured dependencies."""

    return [check_postgres(settings), check_redis(settings)]


def validate_startup_dependencies(settings: Settings) -> None:
    """Fail startup when configured critical dependencies are unavailable."""

    failed_results = [
        result for result in readiness_dependency_results(settings) if not result.ok
    ]
    if not failed_results:
        return

    details = "; ".join(
        f"{result.name}: {result.reason}" for result in failed_results
    )
    raise InfrastructureUnavailableError(
        f"Required infrastructure dependency unavailable: {details}"
    )


def _redis_required(settings: Settings) -> bool:
    return settings.rate_limit_mode == "redis" or settings.callback_replay_mode == "redis"


def _safe_reason(exc: Exception) -> str:
    reason = str(exc).strip()
    if reason == "":
        return exc.__class__.__name__
    return reason.splitlines()[0]

