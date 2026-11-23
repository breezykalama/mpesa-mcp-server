"""Unit tests for canonical approval payload encoding and hashing."""

from __future__ import annotations

import hashlib
import math
from typing import Any

import pytest
from app.approvals.integrity import (
    NonCanonicalPayloadError,
    canonical_payload_hash,
    canonical_payload_json,
)


def test_canonical_encoding_is_key_order_independent() -> None:
    first = {"b": 1, "a": {"y": [1, 2], "x": "é"}}
    second = {"a": {"x": "é", "y": [1, 2]}, "b": 1}

    assert canonical_payload_json(first) == '{"a":{"x":"é","y":[1,2]},"b":1}'
    assert canonical_payload_hash(first) == canonical_payload_hash(second)


def test_hash_is_lowercase_sha256_of_canonical_utf8() -> None:
    payload = {"amount": 100, "phone_number": "254700000000"}
    expected = hashlib.sha256(b'{"amount":100,"phone_number":"254700000000"}').hexdigest()

    assert canonical_payload_hash(payload) == expected
    assert len(expected) == 64 and expected == expected.lower()


def test_hash_changes_when_any_value_changes() -> None:
    payload = {"amount": 100, "phone_number": "254700000000"}

    assert canonical_payload_hash(payload) != canonical_payload_hash({**payload, "amount": 101})


@pytest.mark.parametrize(
    "payload",
    [
        {"amount": 1.5},
        {"amount": math.inf},
        {"lines": (1, 2)},
        {"nested": {2: "x"}},
        {"text": "a\x00b"},
        {"a\x00": 1},
        {"value": object()},
        ["not", "a", "dict"],
    ],
    ids=["float", "inf", "tuple", "int-key", "nul-value", "nul-key", "object", "list"],
)
def test_non_canonical_payloads_rejected(payload: Any) -> None:
    with pytest.raises(NonCanonicalPayloadError):
        canonical_payload_hash(payload)
