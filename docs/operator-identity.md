# Operator Identity

The operator API uses a normalized `OperatorIdentity` model so authentication methods can change without changing approval, RBAC, audit, or operator API behavior.

This foundation supports the current static token mode, a no-network OIDC development placeholder, and standards-based OIDC JWT verification using issuer discovery and JWKS.

Microsoft Entra ID is the primary documented enterprise identity provider example. See [Microsoft Entra ID Integration Guide](entra-id-setup.md) and [Microsoft Entra ID Validation Runbook](entra-validation-runbook.md).

## Identity Model

Every authentication mode resolves credentials into:

- `subject`: stable operator identifier.
- `email`: optional operator email.
- `display_name`: human-readable operator name.
- `groups`: optional external identity-provider groups.
- `roles`: one or more of `viewer`, `approver`, and `admin`.

Existing workflows continue to use the identity subject as `operator_id`. The highest assigned role is used for backwards-compatible RBAC checks.

Audit events can optionally store the operator subject, email, display name, and mapped platform roles. Existing audit records remain valid because these fields are nullable.

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

OIDC provider mode is selected with:

- `OIDC_PROVIDER_MODE=development`
- `OIDC_PROVIDER_MODE=jwks`

`development` is the local/test-safe default. Production deployments must use `jwks`.

### Development Provider

The `DevelopmentOIDCIdentityProvider` performs no external calls and no cryptographic token validation. It accepts a non-empty bearer credential and returns identity claims from safe development configuration:

- `OIDC_DEVELOPMENT_SUBJECT`
- `OIDC_DEVELOPMENT_EMAIL`
- `OIDC_DEVELOPMENT_DISPLAY_NAME`
- `OIDC_DEVELOPMENT_GROUPS`

This provider is intended only for tests and local architecture validation.

### JWKS Provider

The `JWKSOIDCIdentityProvider` verifies bearer JWTs using standard OIDC discovery:

1. Fetch discovery from `{OIDC_ISSUER}/.well-known/openid-configuration`.
2. Read `jwks_uri`.
3. Fetch JWKS.
4. Select the signing key by JWT `kid`.
5. Verify token signature.
6. Validate issuer, audience, expiry, and not-before where present.
7. Extract normalized identity claims.
8. Map group/role claims into platform roles.

JWKS and discovery documents are cached in memory using `OIDC_JWKS_CACHE_SECONDS`.

Supported claim settings:

- `OIDC_EMAIL_CLAIM`
- `OIDC_NAME_CLAIM`
- `OIDC_GROUPS_CLAIM`
- `OIDC_ROLES_CLAIM`

`OIDC_CLOCK_SKEW_SECONDS` controls acceptable JWT clock skew.

Invalid, expired, malformed, unknown-key, wrong-issuer, wrong-audience, and fetch-failure cases fail closed and return an authentication failure.

## Role Mapping

Provider group claims map into the platform roles:

- `viewer`: read operator endpoints.
- `approver`: read operator endpoints and review approvals.
- `admin`: all operator capabilities, including reconciliation runs.

Configured group mappings:

- `OIDC_VIEWER_GROUPS`
- `OIDC_APPROVER_GROUPS`
- `OIDC_ADMIN_GROUPS`

Example:

```text
OIDC_VIEWER_GROUPS=finance_viewers,ops_viewers
OIDC_APPROVER_GROUPS=finance_approvers
OIDC_ADMIN_GROUPS=finance_admins
```

Role inheritance is explicit:

- viewer group -> `viewer`
- approver group -> `viewer`, `approver`
- admin group -> `viewer`, `approver`, `admin`

Multiple matching groups are supported and roles are deduplicated. Unknown groups and empty group lists do not grant platform roles.

Future production mapping should be explicit, least-privilege, and environment-specific. Unknown external groups must not grant access.

## Future Entra ID Integration Notes

Microsoft Entra ID and Google Workspace can use the standards-based JWKS provider when their tokens expose suitable claims and group/app-role mappings. A future provider-specific adapter may still be useful for tenant-specific behavior.

For a full enterprise setup walkthrough, see [Microsoft Entra ID Integration Guide](entra-id-setup.md). For non-production validation against a real tenant, see [Microsoft Entra ID Validation Runbook](entra-validation-runbook.md).

For Entra ID, production setup should:

- use the tenant issuer URL as `OIDC_ISSUER`.
- use the API application/client ID as `OIDC_AUDIENCE`.
- map group IDs or app roles into `OIDC_*_GROUPS`.
- avoid trusting client-supplied display fields without token validation.
- fail closed when group claims are missing or overage handling is required.

For Google Workspace, production setup should:

- use Google's OIDC issuer.
- validate the configured OAuth client audience.
- map suitable groups or hosted-domain claims through explicit platform role settings.
- avoid granting roles from unverified client-side profile data.

No identity-provider secrets should be committed to the repository.
