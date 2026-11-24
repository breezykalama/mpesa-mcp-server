"""Verified human reviewer identity for durable approval decisions."""

from __future__ import annotations

from dataclasses import dataclass

from app.auth.oidc import OperatorIdentity


class ReviewerNotEligibleError(PermissionError):
    """Raised when an identity may not record a durable approval decision."""


@dataclass(frozen=True)
class VerifiedReviewer:
    """A reviewer whose issuer, subject and human status were verified by the IdP.

    Durable decisions have no development exception: only signature- and
    issuer-verified OIDC JWKS tokens that are positively classified as delegated human
    users qualify. Static-token, development, disabled-auth, application (app-only) and
    unclassified principals are rejected, and no issuer is ever synthesized.

    Build instances with ``from_identity`` from a server-side ``OperatorIdentity``; the
    durable repository does this itself so callers cannot supply a pre-built reviewer.
    """

    issuer: str
    subject: str
    email: str | None
    display_name: str
    roles: tuple[str, ...]
    authentication_method: str
    principal_type: str

    @classmethod
    def from_identity(
        cls,
        identity: OperatorIdentity,
        *,
        trusted_issuers: frozenset[str],
    ) -> VerifiedReviewer:
        """Return a verified reviewer or raise ``ReviewerNotEligibleError``."""

        if identity.principal_type == "application":
            raise ReviewerNotEligibleError("Application identities cannot make decisions.")
        if not identity.is_verified_human:
            raise ReviewerNotEligibleError(
                "Durable approval decisions require a verified human operator identity."
            )
        if not identity.has_role("approver"):
            raise ReviewerNotEligibleError("Operator role is not permitted to review approvals.")

        # Stored exactly as verified; whitespace-only values are rejected, never trimmed,
        # so two distinct identities can never be aliased.
        issuer = identity.issuer or ""
        subject = identity.subject
        if not issuer.strip() or not subject.strip():
            raise ReviewerNotEligibleError("Reviewer identity is missing a verified issuer.")
        if issuer not in trusted_issuers:
            raise ReviewerNotEligibleError("Reviewer issuer is not trusted.")

        return cls(
            issuer=issuer,
            subject=subject,
            email=identity.email,
            display_name=identity.display_name,
            roles=tuple(identity.roles),
            authentication_method=identity.authentication_method,
            principal_type=identity.principal_type,
        )


def normalize_trusted_issuers(issuers: frozenset[str] | set[str] | None) -> frozenset[str]:
    """Normalize configured issuers the way the JWKS provider verifies them."""

    return frozenset(issuer.strip().rstrip("/") for issuer in issuers or () if issuer.strip())
