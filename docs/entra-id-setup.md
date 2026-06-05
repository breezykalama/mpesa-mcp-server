# Microsoft Entra ID Integration Guide

## Overview

This platform integrates with Microsoft Entra ID using standards-based OpenID Connect.

Microsoft Entra ID acts as the identity provider. The platform does not require an Azure SDK for operator authentication. It verifies bearer JWTs using:

- OIDC discovery
- JWKS verification
- JWT access tokens
- issuer, audience, expiry, and not-before validation
- group or role claim mapping into platform RBAC roles

Architecture:

```text
User
  |
  v
Microsoft Entra ID
  |
  v
OIDC Token
  |
  v
JWKS Verification
  |
  v
Role Mapping
  |
  v
Operator Dashboard / APIs
```

The platform remains vendor-neutral at runtime. Entra ID is one enterprise identity provider that can work with the existing OIDC/JWKS implementation.

## Prerequisites

- Microsoft Entra tenant access.
- Permission to create or manage an application registration.
- Operator groups created in Entra ID.
- Platform deployed in the target environment.
- HTTPS enabled for operator-facing endpoints.
- `AUTH_MODE=oidc` and `OIDC_PROVIDER_MODE=jwks` planned for the environment.

## Step 1: Create App Registration

In the Azure Portal:

1. Go to **Microsoft Entra ID**.
2. Open **App registrations**.
3. Select **New registration**.
4. Choose a clear operational name.

Recommended example name:

```text
Mpesa Payment Ops Platform
```

Use a name that clearly identifies the environment, for example:

```text
Mpesa Payment Ops Platform - Staging
Mpesa Payment Ops Platform - Production
```

Avoid reusing the same registration across unrelated environments unless your security team has approved that pattern.

## Step 2: Configure Authentication

For the first production rollout, prefer a single-tenant app registration unless there is a clear business need for multi-tenant access.

Redirect URI examples depend on the future frontend login flow. This project currently verifies bearer tokens but does not implement frontend SSO login yet.

Example future redirect URI placeholders:

```text
https://<operator-dashboard-domain>/auth/callback
https://<staging-dashboard-domain>/auth/callback
```

The platform validates JWT `aud` against `OIDC_AUDIENCE`. In Entra ID, this is normally the API application ID URI or client/application ID, depending on how the app registration and exposed API are configured.

Issuer URL format:

```text
https://login.microsoftonline.com/{tenant-id}/v2.0
```

Do not hardcode tenant IDs in documentation, source code, or public examples. Store the real issuer value in environment configuration or a production secret/config system.

## Step 3: Configure Token Claims

Required claims:

- `sub`: stable subject identifier for the operator.
- `email`: operator email address, used for audit context when available.
- `name`: human-readable operator display name.

Recommended claims:

- `preferred_username`: fallback display identifier when `name` or `email` is unavailable.
- `groups`: group IDs or group names used for role mapping.

Why these claims matter:

- `sub` becomes the stable internal operator subject.
- `email` helps operators and auditors identify the human behind an action.
- `name` improves dashboard and audit readability.
- `preferred_username` is a useful fallback for enterprise identity providers.
- `groups` enables least-privilege access control without hardcoding operators in the application.

The platform claim names are configurable:

```text
OIDC_EMAIL_CLAIM=email
OIDC_NAME_CLAIM=name
OIDC_GROUPS_CLAIM=groups
OIDC_ROLES_CLAIM=roles
```

## Step 4: Configure Groups

Create or identify Entra groups for operator access.

Example group strategy:

```text
Finance Viewers
Finance Approvers
Finance Admins
```

Recommended mapping:

```text
Finance Viewers
  -> viewer

Finance Approvers
  -> viewer
  -> approver

Finance Admins
  -> viewer
  -> approver
  -> admin
```

The platform applies role inheritance automatically:

- viewer group grants `viewer`.
- approver group grants `viewer` and `approver`.
- admin group grants `viewer`, `approver`, and `admin`.

Use stable group object IDs for production if possible. Display names are easier to read but can be renamed.

## Step 5: Configure Platform Environment Variables

Use placeholders like these. Do not commit real tenant IDs, client IDs, group IDs, or secrets.

```text
AUTH_MODE=oidc
OIDC_PROVIDER_MODE=jwks

OIDC_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0
OIDC_AUDIENCE=<api-client-id-or-application-id-uri>

OIDC_VIEWER_GROUPS=<finance-viewers-group-id>,<ops-viewers-group-id>
OIDC_APPROVER_GROUPS=<finance-approvers-group-id>
OIDC_ADMIN_GROUPS=<finance-admins-group-id>

OIDC_JWKS_CACHE_SECONDS=3600
OIDC_CLOCK_SKEW_SECONDS=60
OIDC_EMAIL_CLAIM=email
OIDC_NAME_CLAIM=name
OIDC_GROUPS_CLAIM=groups
OIDC_ROLES_CLAIM=roles
```

For local development only, `OIDC_PROVIDER_MODE=development` can be used with safe placeholder groups. Production must use `OIDC_PROVIDER_MODE=jwks`.

## Step 6: Validate Integration

Validation checklist:

- [ ] Startup succeeds with `AUTH_MODE=oidc`.
- [ ] Startup succeeds with `OIDC_PROVIDER_MODE=jwks`.
- [ ] `/health` returns healthy.
- [ ] `/health/ready` returns ready.
- [ ] JWT signature verifies successfully.
- [ ] Issuer validation succeeds.
- [ ] Audience validation succeeds.
- [ ] Required claims are extracted.
- [ ] Groups are mapped into platform roles.
- [ ] Operator dashboard access works for viewer users.
- [ ] Approval endpoints work for approver users.
- [ ] Admin-only endpoints reject non-admin users.
- [ ] Operator subject, email, display name, and mapped roles appear in audit events when available.
- [ ] Invalid tokens return 401 without stack traces or token leakage.

## Step 7: Troubleshooting

### Invalid Issuer

Symptoms:

- Valid-looking token returns 401.
- Logs show OIDC verification failure.

Likely cause:

- `OIDC_ISSUER` does not match the token `iss` claim exactly.
- Tenant ID or `/v2.0` path is wrong.

Resolution:

- Compare `OIDC_ISSUER` with the token issuer.
- Use the Entra tenant issuer format: `https://login.microsoftonline.com/{tenant-id}/v2.0`.

### Invalid Audience

Symptoms:

- Token verifies structurally but authentication fails.

Likely cause:

- `OIDC_AUDIENCE` does not match token `aud`.
- Token was issued for the wrong API or client.

Resolution:

- Confirm the app registration and exposed API configuration.
- Set `OIDC_AUDIENCE` to the expected API audience.

### Expired Token

Symptoms:

- Previously working token starts returning 401.

Likely cause:

- JWT `exp` has passed.
- Client is not refreshing tokens.

Resolution:

- Re-authenticate through the identity provider.
- Confirm token refresh behavior in the future frontend SSO implementation.

### Missing Groups Claim

Symptoms:

- Token verifies, but user has no platform access.

Likely cause:

- Token does not include `groups` or `roles`.
- Group overage behavior requires additional handling.
- The platform is reading the wrong claim name.

Resolution:

- Configure group claims in Entra ID.
- Confirm `OIDC_GROUPS_CLAIM` or `OIDC_ROLES_CLAIM`.
- For group overage scenarios, define a provider-specific strategy before production use.

### Unknown Role Mapping

Symptoms:

- Token verifies, but user receives 401 or 403.

Likely cause:

- User group is not listed in `OIDC_VIEWER_GROUPS`, `OIDC_APPROVER_GROUPS`, or `OIDC_ADMIN_GROUPS`.

Resolution:

- Add the correct group object ID to the appropriate environment variable.
- Prefer least privilege. Do not map broad tenant-wide groups to admin.

### JWKS Fetch Failure

Symptoms:

- All OIDC users fail authentication.
- Logs show JWKS retrieval or key selection failure.

Likely cause:

- Network/DNS issue.
- Entra endpoint unavailable.
- `jwks_uri` from discovery cannot be reached.

Resolution:

- Confirm outbound network access from the platform.
- Confirm the discovery document is reachable from the runtime environment.
- Check firewall, proxy, and DNS rules.

### Discovery Fetch Failure

Symptoms:

- All OIDC users fail before key lookup.

Likely cause:

- Incorrect `OIDC_ISSUER`.
- Network path to Entra ID is blocked.
- Tenant issuer URL is unavailable.

Resolution:

- Confirm the issuer URL.
- Confirm runtime outbound HTTPS access.
- Validate discovery manually from the deployment environment.

## Step 8: Security Considerations

- Apply the principle of least privilege.
- Use group-based access control rather than individual ad hoc operator grants.
- Keep admin membership small and reviewed.
- Use short-lived access tokens.
- Store production configuration and secrets outside source control.
- Do not commit tenant IDs, client IDs, group IDs, or credentials if they are considered sensitive in your organization.
- Require HTTPS for dashboard and API access.
- Never log raw bearer tokens.
- Use audit events to review operator activity, approval decisions, and privileged actions.
- Validate role mappings in staging before production use.

## Step 9: Production Readiness Checklist

- [ ] Entra groups created.
- [ ] Operator group membership reviewed.
- [ ] App registration created.
- [ ] OIDC issuer configured.
- [ ] OIDC audience configured.
- [ ] Group claim configured.
- [ ] Platform role mapping configured.
- [ ] `AUTH_MODE=oidc` enabled.
- [ ] `OIDC_PROVIDER_MODE=jwks` enabled.
- [ ] Startup validation passes.
- [ ] Readiness endpoint is healthy.
- [ ] Viewer access tested.
- [ ] Approver access tested.
- [ ] Admin access tested.
- [ ] Invalid token path tested.
- [ ] Audit trail verified.
- [ ] Production callback security enabled.
- [ ] Trusted proxy configured for callbacks.
- [ ] Operator auth validation completed before real-money testing.
