"""Tests for rate limiter implementations."""

from app.rate_limit.limiter import InMemoryRateLimiter, RedisRateLimiter


def test_in_memory_rate_limiter_allows_until_limit() -> None:
    limiter = InMemoryRateLimiter()

    first = limiter.allow(key="tool:subject", limit=2, window_seconds=60)
    second = limiter.allow(key="tool:subject", limit=2, window_seconds=60)

    assert first.allowed is True
    assert second.allowed is True
    assert second.remaining == 0


def test_in_memory_rate_limiter_blocks_after_limit() -> None:
    limiter = InMemoryRateLimiter()

    limiter.allow(key="tool:subject", limit=1, window_seconds=60)
    blocked = limiter.allow(key="tool:subject", limit=1, window_seconds=60)

    assert blocked.allowed is False
    assert blocked.remaining == 0


def test_redis_rate_limiter_allows_within_limit() -> None:
    redis_client = FakeRedisClient()
    limiter = RedisRateLimiter(redis_url="redis://localhost:6379/0", redis_client=redis_client)

    first = limiter.allow(key="tool:subject", limit=2, window_seconds=60)
    second = limiter.allow(key="tool:subject", limit=2, window_seconds=60)

    assert first.allowed is True
    assert second.allowed is True
    assert second.remaining == 0


def test_redis_rate_limiter_blocks_after_limit() -> None:
    redis_client = FakeRedisClient()
    limiter = RedisRateLimiter(redis_url="redis://localhost:6379/0", redis_client=redis_client)

    limiter.allow(key="tool:subject", limit=1, window_seconds=60)
    blocked = limiter.allow(key="tool:subject", limit=1, window_seconds=60)

    assert blocked.allowed is False
    assert blocked.remaining == 0


def test_redis_rate_limiter_sets_expiry_on_first_request() -> None:
    redis_client = FakeRedisClient()
    limiter = RedisRateLimiter(redis_url="redis://localhost:6379/0", redis_client=redis_client)

    limiter.allow(key="tool:subject", limit=2, window_seconds=60)
    limiter.allow(key="tool:subject", limit=2, window_seconds=60)

    assert redis_client.expiry_calls == [("tool:subject", 60)]


class FakeRedisClient:
    """Redis client fake for unit tests."""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.expiry_calls: list[tuple[str, int]] = []

    def incr(self, name: str) -> int:
        self.values[name] = self.values.get(name, 0) + 1
        return self.values[name]

    def expire(self, name: str, time: int) -> bool:
        self.ttls[name] = time
        self.expiry_calls.append((name, time))
        return True

    def ttl(self, name: str) -> int:
        return self.ttls.get(name, -1)
