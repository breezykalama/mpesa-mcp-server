"""Canonical payment-payload encoding and integrity hashing for durable approvals."""

from __future__ import annotations

import hashlib
import json
from typing import Any

PAYLOAD_HASH_ALGORITHM = "sha256"
"""Hash algorithm recorded in ``approval_requests.payload_hash_algorithm``."""


class NonCanonicalPayloadError(ValueError):
    """Raised when a payload cannot be stored and read back exactly as JSON."""


def canonical_payload_json(payload: dict[str, Any]) -> str:
    """Return the canonical JSON encoding of a payment payload.

    Keys are sorted, separators are compact, non-ASCII is kept verbatim, and
    ``NaN``/``Infinity`` are rejected. The payload must be JSON-native: decoding the
    encoding must reproduce ``payload`` exactly. That rules out tuples, non-string keys
    and other values JSON would silently coerce, so the stored document is precisely
    what the requester submitted.

    Floating-point numbers are rejected: monetary amounts must be integers (or strings),
    and PostgreSQL JSONB normalizes number text (``1e20`` reads back as an integer),
    which would break exact round-tripping and hash verification. NUL characters are
    rejected because PostgreSQL text cannot store them.
    """

    if not isinstance(payload, dict):
        raise NonCanonicalPayloadError("Payment payload must be a JSON object.")
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise NonCanonicalPayloadError("Payment payload is not JSON-serializable.") from exc

    if json.loads(encoded) != payload or not _is_json_native(payload):
        raise NonCanonicalPayloadError(
            "Payment payload must contain only JSON-native, non-float values."
        )
    return encoded


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    """Return the lowercase hex SHA-256 digest of the canonical payload encoding."""

    return hashlib.sha256(canonical_payload_json(payload).encode("utf-8")).hexdigest()


def _is_json_native(value: object) -> bool:
    if isinstance(value, str):
        return "\x00" not in value
    if value is None or isinstance(value, bool | int):
        return True
    if isinstance(value, list):
        return all(_is_json_native(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and "\x00" not in key and _is_json_native(item)
            for key, item in value.items()
        )
    return False
