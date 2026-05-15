"""Tests for callback replay protection."""

from __future__ import annotations

from app.callbacks.replay import (
    InMemoryReplayProtection,
    RedisReplayProtection,
    build_callback_replay_key,
)


def test_in_memory_replay_protection_blocks_duplicate_key() -> None:
    replay = InMemoryReplayProtection()
    key = build_callback_replay_key(callback_payload())

    first = replay.check_and_store(key=key, window_seconds=600)
    duplicate = replay.check_and_store(key=key, window_seconds=600)

    assert first.allowed is True
    assert duplicate.allowed is False
    assert duplicate.reason == "Duplicate callback replay detected."


def test_replay_key_is_deterministic() -> None:
    first = build_callback_replay_key(callback_payload())
    second = build_callback_replay_key(callback_payload())

    assert first == second
    assert first.startswith("callback_replay:")


def test_redis_replay_protection_works_with_fake_redis() -> None:
    redis_client = FakeReplayRedisClient()
    replay = RedisReplayProtection(
        redis_url="redis://localhost:6379/0",
        redis_client=redis_client,
    )
    key = build_callback_replay_key(callback_payload())

    first = replay.check_and_store(key=key, window_seconds=600)
    duplicate = replay.check_and_store(key=key, window_seconds=600)

    assert first.allowed is True
    assert duplicate.allowed is False
    assert redis_client.set_calls == [
        (key, "1", 600, True),
        (key, "1", 600, True),
    ]


class FakeReplayRedisClient:
    """Redis replay fake for unit tests."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, int, bool]] = []

    def set(self, name: str, value: str, *, ex: int, nx: bool) -> bool | None:
        self.set_calls.append((name, value, ex, nx))
        if nx and name in self.values:
            return None

        self.values[name] = value
        return True


def callback_payload() -> dict[str, object]:
    return {
        "Body": {
            "stkCallback": {
                "CheckoutRequestID": "ws_CO_REPLAY",
                "ResultCode": 0,
                "CallbackMetadata": {
                    "Item": [
                        {"Name": "MpesaReceiptNumber", "Value": "RCPREPLAY"},
                    ]
                },
            }
        }
    }
