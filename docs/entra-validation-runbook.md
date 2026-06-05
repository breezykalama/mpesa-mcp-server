# Microsoft Entra ID Validation Runbook

## Objective

Validate the platform against a real Microsoft Entra ID tenant in a non-production environment.

This runbook verifies that the existing standards-based OIDC/JWKS implementation works with real Entra-issued tokens. It does not enable production access by itself, and it does not require Azure SDKs, production credentials, or frontend SSO login.

## Success Criteria

Validation succeeds only when all of these are true:

- Startup succeeds with OIDC/JWKS configuration.
- OIDC discovery document loads from the configured issuer.
- JWKS loads from the discovered `jwks_uri`.
- A real Entra JWT validates successfully.
- Token groups or roles are extracted.
- Groups are mapped to platform roles.
- Protected operator endpoints enforce role access.
- Operator identity appears in audit events.

## Validation Setup

Use a non-production Entra tenant or a dedicated test tenant.

Required setup:

1. Create or use a test Microsoft Entra tenant.
2. Create a test app registration for the platform.
3. Create test operator groups:

```text
Finance Viewers
Finance Approvers
Finance Admins
```

4. Assign test users:

```text
viewer.test@example.test       -> Finance Viewers
approver.test@example.test     -> Finance Approvers
admin.test@example.test        -> Finance Admins
```

Use placeholder accounts or internal test accounts only. Do not use production payment operators for the first validation pass.

## Environment Configuration

Configure the platform with placeholder-shaped values like these. Replace placeholders only in your local or deployed non-production environment.

```text
AUTH_MODE=oidc
OIDC_PROVIDER_MODE=jwks

OIDC_ISSUER=https://login.microsoftonline.com/<test-tenant-id>/v2.0
OIDC_AUDIENCE=<test-api-client-id-or-application-id-uri>

OIDC_VIEWER_GROUPS=<finance-viewers-group-id>
OIDC_APPROVER_GROUPS=<finance-approvers-group-id>
OIDC_ADMIN_GROUPS=<finance-admins-group-id>

OIDC_GROUPS_CLAIM=groups
OIDC_ROLES_CLAIM=roles
OIDC_EMAIL_CLAIM=email
OIDC_NAME_CLAIM=name
```

Do not commit real tenant IDs, client IDs, group IDs, access tokens, or credentials.

## Token Validation Procedure

### 1. Start The Platform

Start the platform with the non-production OIDC environment variables.

Validate:

```text
GET /health
GET /health/ready
```

Expected result:

- `/health` returns healthy.
- `/health/ready` returns ready.
- startup logs do not show configuration validation errors.

### 2. Obtain A Test Token

Obtain an access token from the test Entra app registration using an approved non-production flow.

The token must be issued by the configured `OIDC_ISSUER` and have an audience matching `OIDC_AUDIENCE`.

Do not paste access tokens into source files, docs, commits, issue trackers, or screenshots.

### 3. Call A Protected Endpoint

Call a viewer endpoint with the token:

```text
GET /operator/transactions
Authorization: Bearer <test-access-token>
```

Expected result:

- Viewer user succeeds.
- Approver user succeeds.
- Admin user succeeds.
- Invalid or missing token returns 401.

### 4. Verify Role Mapping

Test each group:

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

Validation examples:

- Viewer can call read-only operator endpoints.
- Viewer cannot approve/reject payment approvals.
- Approver can approve/reject payment approvals.
- Approver cannot call admin-only reconciliation run.
- Admin can call admin-only reconciliation run.

### 5. Verify Operator Identity

Confirm the platform extracts:

- subject
- email
- display name
- groups
- mapped roles

Use logs and audit events. Do not log or store raw tokens.

### 6. Verify Audit Records

Trigger an operator action that writes audit activity, such as an approval action in a safe non-production workflow.

Verify audit records include:

- `operator_subject`
- `operator_email`
- `operator_display_name`
- `operator_roles`
- `correlation_id`
- event type
- timestamp

Existing audit records may have null operator identity fields. New OIDC-authenticated operator actions should include identity fields where available.

## Failure Scenarios

### Invalid Issuer

Expected result:

- Authentication fails.
- Protected endpoint returns 401.
- No operator identity is created.

Likely cause:

- `OIDC_ISSUER` does not match the token `iss`.
- Wrong tenant or missing `/v2.0`.

### Invalid Audience

Expected result:

- Authentication fails.
- Protected endpoint returns 401.

Likely cause:

- `OIDC_AUDIENCE` does not match token `aud`.
- Token was issued for a different app/API.

### Expired Token

Expected result:

- Authentication fails.
- Protected endpoint returns 401.

Likely cause:

- Token `exp` has passed.

### Missing Groups

Expected result:

- Token may verify cryptographically.
- Platform access is denied because no platform roles can be mapped.

Likely cause:

- Entra group claim is not configured.
- User is not assigned to a mapped group.
- Group overage behavior is present.

### Unknown Group Mapping

Expected result:

- Token verifies.
- Access is denied or role is insufficient.

Likely cause:

- Token contains a group that is not listed in `OIDC_VIEWER_GROUPS`, `OIDC_APPROVER_GROUPS`, or `OIDC_ADMIN_GROUPS`.

### JWKS Retrieval Failure

Expected result:

- Authentication fails closed.
- Protected endpoint returns 401.

Likely cause:

- Platform cannot reach the discovered `jwks_uri`.
- Network, DNS, proxy, or firewall issue.

### Discovery Retrieval Failure

Expected result:

- Authentication fails closed.
- Protected endpoint returns 401.

Likely cause:

- `OIDC_ISSUER` is wrong.
- Platform cannot reach `{OIDC_ISSUER}/.well-known/openid-configuration`.

## Audit Validation

For an OIDC-authenticated operator action, verify:

```text
operator_subject=<token-subject>
operator_email=<token-email>
operator_display_name=<token-name-or-fallback>
operator_roles=["viewer", "..."]
```

Validation checklist:

- [ ] Subject is stable and not empty.
- [ ] Email appears when present in the token.
- [ ] Display name appears when present in the token.
- [ ] Roles match the configured group mapping.
- [ ] Correlation ID is present.
- [ ] Raw bearer token is not logged.

## Production Checklist

Before enabling OIDC in production, confirm:

- [ ] Non-production Entra validation completed.
- [ ] App registration ownership is documented.
- [ ] Production tenant issuer is confirmed.
- [ ] Production API audience is confirmed.
- [ ] Group claim behavior is confirmed.
- [ ] Group overage behavior is understood.
- [ ] Viewer, approver, and admin groups are reviewed.
- [ ] Least-privilege role mapping is approved.
- [ ] Break-glass/admin process is documented.
- [ ] Audit records include operator identity fields.
- [ ] Raw tokens are not logged.
- [ ] HTTPS is enforced.
- [ ] `AUTH_MODE=oidc` is configured.
- [ ] `OIDC_PROVIDER_MODE=jwks` is configured.
- [ ] Production callback security remains enabled.
- [ ] Trusted proxy callback verification remains enabled.
- [ ] Rollback plan exists for identity-provider outages.
