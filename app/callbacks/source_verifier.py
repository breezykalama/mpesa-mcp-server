"""Callback source verification abstractions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class CallbackSourceVerificationDecision:
    """Decision returned by callback source verification."""

    allowed: bool
    reason: str


class CallbackSourceVerifierProtocol(Protocol):
    """Interface for callback source verification strategies."""

    def verify(self, payload: dict[str, Any]) -> CallbackSourceVerificationDecision:
        """Return whether a callback source is trusted."""


class DevelopmentCallbackSourceVerifier:
    """Development verifier that intentionally allows callbacks."""

    def verify(self, payload: dict[str, Any]) -> CallbackSourceVerificationDecision:
        """Allow callbacks in development and local mock mode."""

        return CallbackSourceVerificationDecision(
            allowed=True,
            reason="Development callback source verifier allowed request.",
        )


class StrictPlaceholderCallbackSourceVerifier:
    """Placeholder that rejects until real provider/deployment verification exists."""

    def verify(self, payload: dict[str, Any]) -> CallbackSourceVerificationDecision:
        """Reject callbacks because no production source verification is configured."""

        return CallbackSourceVerificationDecision(
            allowed=False,
            reason="Strict callback source verification is not configured.",
        )

