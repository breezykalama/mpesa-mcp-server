"""Swappable rate limiter implementations."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import monotonic
from typing import Protocol, cast

from redis import Redis

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitDecision:
    """Decision returned by a rate limiter."""

    allowed: bool
    key: str
    limit: int
    remaining: int
    reset_after_seconds: int


class RateLimiterProtocol(Protocol):
    """Interface for rate limiter implementations."""

    def allow(self, *, key: str, limit: int, window_seconds: int) -> RateLimitDecision:
        """Return whether a key is allowed for the configured limit window."""


class RedisClientProtocol(Protocol):
    """Small Redis client contract used by RedisRateLimiter."""

    def incr(self, name: str) -> int:
        """Increment and return the value for a key."""

    def expire(self, name: str, time: int) -> bool:
        """Set key expiry in seconds."""

    def ttl(self, name: str) -> int:
        """Return key time-to-live in seconds."""


class InMemoryRateLimiter:
    """Simple fixed-window in-memory rate limiter."""

    def __init__(self) -> None:
        self._windows: dict[str, tuple[float, int]] = {}

    def allow(self, *, key: str, limit: int, window_seconds: int) -> RateLimitDecision:
        """Return whether a key is allowed for the configured limit window."""

        now = monotonic()
        window_start, count = self._windows.get(key, (now, 0))
        elapsed = now - window_start

        if elapsed >= window_seconds:
            window_start = now
            count = 0

        if count >= limit:
            logger.warning(
                "Rate limit exceeded.",
                extra={
                    "event_type": "rate_limit_exceeded",
                    "limit": limit,
                    "remaining": 0,
                    "reset_after_seconds": max(0, int(window_seconds - elapsed)),
                },
            )
            return RateLimitDecision(
                allowed=False,
                key=key,
                limit=limit,
                remaining=0,
                reset_after_seconds=max(0, int(window_seconds - elapsed)),
            )

        count += 1
        self._windows[key] = (window_start, count)
        return RateLimitDecision(
            allowed=True,
            key=key,
            limit=limit,
            remaining=max(0, limit - count),
            reset_after_seconds=max(0, int(window_seconds - (now - window_start))),
        )


class RedisRateLimiter:
    """Redis-backed fixed-window rate limiter."""

    def __init__(
        self,
        *,
        redis_url: str,
        redis_client: RedisClientProtocol | None = None,
    ) -> None:
        self._redis: RedisClientProtocol = redis_client or cast(
            RedisClientProtocol,
            Redis.from_url(redis_url, decode_responses=True),
        )

    def allow(self, *, key: str, limit: int, window_seconds: int) -> RateLimitDecision:
        """Return whether a key is allowed for the configured limit window."""

        count = self._redis.incr(key)
        if count == 1:
            self._redis.expire(key, window_seconds)

        ttl = self._redis.ttl(key)
        reset_after_seconds = window_seconds if ttl < 0 else ttl

        if count > limit:
            logger.warning(
                "Rate limit exceeded.",
                extra={
                    "event_type": "rate_limit_exceeded",
                    "limit": limit,
                    "remaining": 0,
                    "reset_after_seconds": reset_after_seconds,
                },
            )
            return RateLimitDecision(
                allowed=False,
                key=key,
                limit=limit,
                remaining=0,
                reset_after_seconds=reset_after_seconds,
            )

        return RateLimitDecision(
            allowed=True,
            key=key,
            limit=limit,
            remaining=max(0, limit - count),
            reset_after_seconds=reset_after_seconds,
        )
