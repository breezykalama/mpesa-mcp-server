# Operator Identity

The operator API uses a normalized `OperatorIdentity` model so authentication methods can change without changing approval, RBAC, audit, or operator API behavior.

This foundation supports the current static token mode and a no-network OIDC development placeholder. It does not yet validate real identity-provider tokens.

## Identity Model

Every authentication mode resolves credentials into:

- `subject`: stable operator identifier.
- `email`: optional operator email.
- `display_name`: human-readable operator name.
- `roles`: one or more of `viewer`, `approver`, and `admin`.

Existing workflows continue to use the identity subject as `operator_id`. The highest assigned role is used for backwards-compatible RBAC checks.

Audit events can optionally store the operator subject, email, and display name. Existing audit records remain valid because these fields are nullable.

## Token Mode

`AUTH_MODE=token` is the default and preserves the existing bearer-token behavior.

Configured values:

- `OPERATOR_VIEWER_TOKEN`
- `OPERATOR_APPROVER_TOKEN`
- `OPERATOR_ADMIN_TOKEN`

Each configured token maps to a normalized operator identity and its existing role. Raw tokens must never be logged or committed.

## OIDC Foundation

`AUTH_MODE=oidc` requires:

- `OIDC_ISSUER`
- `OIDC_AUDIENCE`

The current `DevelopmentOIDCIdentityProvider` performs no external calls and no cryptographic token validation. It accepts a non-empty bearer credential and returns identity claims from safe development configuration:

- `OIDC_DEVELOPMENT_SUBJECT`
- `OIDC_DEVELOPMENT_EMAIL`
- `OIDC_DEVELOPMENT_DISPLAY_NAME`
- `OIDC_DEVELOPMENT_ROLES`

This provider is intended only for tests and local architecture validation. It must be replaced by a production OIDC adapter that validates token signature, issuer, audience, expiry, and required claims before OIDC mode is used with real operators.

## Role Mapping

Provider claims must ultimately map into the platform roles:

- `viewer`: read operator endpoints.
- `approver`: read operator endpoints and review approvals.
- `admin`: all operator capabilities, including reconciliation runs.

Future production mapping should be explicit, least-privilege, and environment-specific. Unknown external roles must not grant access.
