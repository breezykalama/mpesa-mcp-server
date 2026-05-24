"""Callback source verification abstractions."""

from __future__ import annotations

from dataclasses import dataclass
from secrets import compare_digest
from typing import Any, Protocol


@dataclass(frozen=True)
class CallbackSourceVerificationDecision:
    """Decision returned by callback source verification."""

    allowed: bool
    reason: str


class CallbackSourceVerifierProtocol(Protocol):
    """Interface for callback source verification strategies."""

    def verify(
        self,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> CallbackSourceVerificationDecision:
        """Return whether a callback source is trusted."""


class DevelopmentCallbackSourceVerifier:
    """Development verifier that intentionally allows callbacks.

    This mode is unsafe for production. It exists only for local development and tests.
    """

    def verify(
        self,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> CallbackSourceVerificationDecision:
        """Allow callbacks in development and local mock mode."""

        return CallbackSourceVerificationDecision(
            allowed=True,
            reason="Development callback source verifier allowed request.",
        )


class StrictBlockCallbackSourceVerifier:
    """Fail-closed verifier that rejects all callbacks."""

    def verify(
        self,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> CallbackSourceVerificationDecision:
        """Reject callbacks in strict block mode."""

        return CallbackSourceVerificationDecision(
            allowed=False,
            reason="Callback source verification is in strict block mode.",
        )


class TrustedProxyCallbackSourceVerifier:
    """Verify callbacks using a trusted reverse proxy injected header.

    Expected deployment model:
    Safaricom -> reverse proxy/API gateway/ingress -> app.
    The proxy verifies the inbound boundary and injects a shared secret header.
    The app rejects public requests that do not carry the trusted header.
    """

    def __init__(self, *, header_name: str, shared_secret: str) -> None:
        self._header_name = header_name.lower()
        self._shared_secret = shared_secret

    def verify(
        self,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> CallbackSourceVerificationDecision:
        """Verify the trusted proxy header."""

        provided_secret = headers.get(self._header_name)
        if provided_secret is None:
            return CallbackSourceVerificationDecision(
                allowed=False,
                reason="Trusted callback proxy header is missing.",
            )

        if not compare_digest(provided_secret, self._shared_secret):
            return CallbackSourceVerificationDecision(
                allowed=False,
                reason="Trusted callback proxy header is invalid.",
            )

        return CallbackSourceVerificationDecision(
            allowed=True,
            reason="Trusted callback proxy header validated.",
        )
