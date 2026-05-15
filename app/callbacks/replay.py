"""Callback replay protection implementations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from time import monotonic
from typing import Any, Protocol, cast

from redis import Redis


@dataclass(frozen=True)
class ReplayDecision:
    """Decision returned by callback replay protection."""

    allowed: bool
    key: str
    reason: str


class ReplayProtectionProtocol(Protocol):
    """Interface for callback replay protection stores."""

    def check_and_store(self, *, key: str, window_seconds: int) -> ReplayDecision:
        """Store a callback replay key if it has not already been seen."""


class RedisReplayClientProtocol(Protocol):
    """Small Redis client contract required by RedisReplayProtection."""

    def set(
        self,
        name: str,
        value: str,
        *,
        ex: int,
        nx: bool,
    ) -> bool | None:
        """Set a key with expiry only if it does not exist."""


class InMemoryReplayProtection:
    """In-memory callback replay protection for local development and tests."""

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}

    def check_and_store(self, *, key: str, window_seconds: int) -> ReplayDecision:
        """Store a callback replay key if it has not already been seen."""

        now = monotonic()
        expires_at = self._seen.get(key)
        if expires_at is not None and expires_at > now:
            return ReplayDecision(
                allowed=False,
                key=key,
                reason="Duplicate callback replay detected.",
            )

        self._seen[key] = now + window_seconds
        self._prune_expired(now)
        return ReplayDecision(
            allowed=True,
            key=key,
            reason="Callback replay key accepted.",
        )

    def _prune_expired(self, now: float) -> None:
        expired_keys = [key for key, expires_at in self._seen.items() if expires_at <= now]
        for key in expired_keys:
            self._seen.pop(key, None)


class RedisReplayProtection:
    """Redis-backed callback replay protection."""

    def __init__(
        self,
        *,
        redis_url: str,
        redis_client: RedisReplayClientProtocol | None = None,
    ) -> None:
        self._redis: RedisReplayClientProtocol = redis_client or cast(
            RedisReplayClientProtocol,
            Redis.from_url(redis_url, decode_responses=True),
        )

    def check_and_store(self, *, key: str, window_seconds: int) -> ReplayDecision:
        """Store a callback replay key if it has not already been seen."""

        stored = self._redis.set(key, "1", ex=window_seconds, nx=True)
        if stored:
            return ReplayDecision(
                allowed=True,
                key=key,
                reason="Callback replay key accepted.",
            )

        return ReplayDecision(
            allowed=False,
            key=key,
            reason="Duplicate callback replay detected.",
        )


def build_callback_replay_key(payload: dict[str, Any]) -> str:
    """Build a deterministic replay key from stable M-Pesa callback identifiers."""

    callback = _extract_callback(payload)
    metadata = _extract_metadata(callback)
    fingerprint_payload = {
        "CheckoutRequestID": _string_or_empty(callback.get("CheckoutRequestID")),
        "ResultCode": _string_or_empty(callback.get("ResultCode")),
        "MpesaReceiptNumber": _string_or_empty(metadata.get("MpesaReceiptNumber")),
    }
    canonical_payload = json.dumps(
        fingerprint_payload,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
    return f"callback_replay:{digest}"


def _extract_callback(payload: dict[str, Any]) -> dict[str, Any]:
    body = payload.get("Body")
    if isinstance(body, dict):
        callback = body.get("stkCallback")
        if isinstance(callback, dict):
            return callback

    callback = payload.get("stkCallback")
    if isinstance(callback, dict):
        return callback

    return payload


def _extract_metadata(callback: dict[str, Any]) -> dict[str, Any]:
    metadata = callback.get("CallbackMetadata")
    if not isinstance(metadata, dict):
        return {}

    items = metadata.get("Item")
    if not isinstance(items, list):
        return {}

    parsed: dict[str, Any] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("Name")
        if isinstance(name, str) and "Value" in item:
            parsed[name] = item["Value"]
    return parsed


def _string_or_empty(value: Any) -> str:
    if value is None:
        return ""
    return str(value)
