# Security Model

This project is a production-shaped prototype for safe agent-assisted payment workflows. It is not ready for live production credentials until the remaining production readiness items are complete.

## Secret Handling

- Do not commit real Daraja credentials.
- Do not commit operator tokens.
- Do not commit callback shared secrets.
- Do not commit database or Redis production credentials.
- Keep local secrets in `.env`, which is ignored by Git.
- Keep `.env.example` placeholder-only.
- Use environment variables as the supported runtime secret source for now.
- Use a real secret manager before production launch.

## Production Configuration Expectations

Startup validation fails fast when critical runtime settings are missing or unsafe.

Required before production-mode Daraja startup:

- `DARAJA_PRODUCTION_CONSUMER_KEY`
- `DARAJA_PRODUCTION_CONSUMER_SECRET`
- `DARAJA_PRODUCTION_PASSKEY`
- `DARAJA_PRODUCTION_SHORTCODE`
- `DARAJA_PRODUCTION_CALLBACK_URL`

When `OPERATOR_AUTH_ENABLED=true`, at least one operator token must be configured.

Outside development-like environments, `CALLBACK_SHARED_SECRET` must be configured.

Redis-backed modes require a valid `REDIS_URL`, and Postgres storage requires a PostgreSQL `DATABASE_URL`.

## Approval Authority

AI agents may request payments, but they must never authorize financial actions.

- No MCP tool approves or rejects approval requests. `approve_payment_request` and `reject_payment_request` are listed in `AGENT_FORBIDDEN_TOOLS` and are denied unconditionally; configuration cannot re-enable them, and the MCP server refuses to register them.
- Approve and reject decisions are only available through the operator approval API, and the reviewer is always the server-authenticated `OperatorIdentity`. Request bodies cannot supply reviewer identity, and there is no fallback or synthetic reviewer.
- Every identity records its `authentication_method` (`oidc_jwks`, `oidc_development`, `static_token`, `auth_disabled`) and `principal_type` (`human`, `application`, `unknown`). Only `oidc_jwks` with a delegated user token (`scp` claim, not `idtyp=app`) counts as a verified human.
- Outside development environments, only verified humans may approve or reject. App-only identities are always denied. Multi-reviewer quorums count only verified humans in every environment, because shared tokens and development identities cannot prove distinct reviewers.
- Rejection is only possible from `pending`.
- Decision and denied-attempt audit events record the reviewer's subject, email, display name, and roles. Raw tokens, credentials, and raw identity-provider claims are never recorded.

Known limitations, tracked in the production readiness checklist:

- Approvals are stored in memory per process. MCP-originated approvals cannot be completed by human operators in the separate API process; this is intentionally left blocked.
- Approval decisions, expiry, and approved-payment execution are serialized with one process-local lock. The lock is held for the duration of the payment-provider call, so a slow provider can temporarily block approval listing and expiry in the same process. It does not coordinate across processes or workers: running multiple API workers or processes is not safe for approvals. The lock can only be removed once durable, transactional approval state and execution claims exist.
- Reviewer eligibility is evaluated before any approval state handling. Ineligible principals receive `403 reviewer_not_authorized`, see no approval details, and cannot trigger expiry or any other state change.

## Callback Source Verification

The app does not claim to cryptographically verify Safaricom callbacks directly.

Production deployments should use a trusted infrastructure boundary:

```text
Safaricom callback
  -> reverse proxy / API gateway / ingress / edge worker
  -> trusted proxy injects a secret header
  -> application validates the trusted header
```

Supported callback source modes:

- `development`: allows callbacks for local development only. Unsafe for production.
- `strict_block`: rejects all callbacks. Safe fail-closed mode.
- `trusted_proxy`: requires `TRUSTED_PROXY_SHARED_SECRET` and validates `TRUSTED_PROXY_HEADER_NAME`, defaulting to `X-Trusted-Callback-Proxy`.

Suitable trusted proxy options include Nginx, Traefik, Cloudflare Workers, API Gateway, or an ingress controller. The app should not be exposed in a way that allows arbitrary public requests to inject the trusted header.

The trusted proxy shared secret should be rotated like any other production secret.

## Dependency Failure Policy

Configured critical dependencies are validated at startup.

- If `STORAGE_MODE=postgres` and PostgreSQL is unavailable, startup fails.
- If Redis-backed rate limiting or callback replay protection is enabled and Redis is unavailable, startup fails.
- Runtime database failures are returned as safe service-unavailable responses.
- Runtime Redis failures fail closed for safety-sensitive paths.
- If callback replay protection is unavailable, callbacks are rejected.
- If rate limiting is unavailable, sensitive MCP tool calls are rejected.

The platform should not silently disable safety controls in production-like modes.

## CI Secret Scanning

GitHub Actions runs gitleaks against the checked-out repository. The scan is intended to catch accidental credentials before they are merged or pushed further.

The current scan checks the working tree/current checkout. Full historical scanning can be added later before production credential handling begins.

## Rotation Expectations

Production launch should include a documented rotation process for:

- Daraja production credentials
- callback shared secret
- operator identity credentials or SSO client secrets
- database credentials
- Redis credentials

No production credential should be long-lived without an owner, expiry/rotation plan, and incident response path.
