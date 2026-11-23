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

## Production Configuration Safety Gate

`app.deployment.classify_deployment` is the single source of truth for which controls a configuration must meet, and `app.config_validation.validate_startup_settings` is the single validator. Classification does not rely on `APP_ENV` alone. Mode-like settings (`APP_ENV`, `DARAJA_MODE`, `STORAGE_MODE`, `RATE_LIMIT_MODE`, `CALLBACK_REPLAY_MODE`, `CALLBACK_SOURCE_VERIFICATION_MODE`, `AUTH_MODE`, `OIDC_PROVIDER_MODE`, `PAYMENT_PROVIDER`) are canonicalized to trimmed lower case when settings load, so classification, validation, container construction, and the Daraja client all see the same value. A blank `APP_ENV` means unset.

### Deployment tiers

| Tier | How it is selected | What it allows |
| --- | --- | --- |
| Local development | `APP_ENV` in `development`, `dev`, `local`, `test` (case-insensitive), or `APP_ENV` unset **with `DARAJA_MODE=mock` only** | Mock or sandbox Daraja, in-memory components, disabled operator auth, static tokens, development OIDC, development callback verification, and development-only single-reviewer approvals |
| Non-production testing | `APP_ENV=staging` | Sandbox or mock Daraja. Requires `CALLBACK_SHARED_SECRET`. No development approval authority: approval decisions need verified human identities |
| Production-grade | `APP_ENV` in `production`, `prod`; **or** `DARAJA_MODE=production` (whatever `APP_ENV` says); **or** a sandbox or production Daraja base URL that is not the official endpoint (see [Daraja endpoint trust policy](#daraja-endpoint-trust-policy)); **or** any unknown `APP_ENV` or `DARAJA_MODE` value (fail closed) | Only configurations that meet every production-grade control below |

Contradictions are rejected outright:

- `DARAJA_MODE=production` requires `APP_ENV=production` (or `prod`) to be set explicitly.
- `APP_ENV` must be set explicitly unless `DARAJA_MODE=mock`.
- `APP_ENV` must be a recognized value.
- `DARAJA_SANDBOX_BASE_URL` / `DARAJA_PRODUCTION_BASE_URL` must be the official endpoint for the active mode.
- Outside local development, legacy unprefixed `DARAJA_*` credentials must not be relied on, and `DARAJA_PRODUCTION_*` credentials must not be configured unless `DARAJA_MODE=production`.

### Daraja endpoint trust policy

Daraja endpoints are an allowlist (`app.daraja.configuration`), shared by classification, validation, and `RealDarajaClient`:

| `DARAJA_MODE` | Only permitted endpoint |
| --- | --- |
| `sandbox` | `https://sandbox.safaricom.co.ke` |
| `production` | `https://api.safaricom.co.ke` |
| `mock` | No network endpoint; use this for local stubs |

Comparison happens after canonicalization (trimmed, lower-cased host, optional single trailing `/`). Everything else is rejected, including custom gateways and reverse proxies, IP literals and every numeric address notation (leading-zero, octal, hexadecimal, short-form, and integer forms), explicit ports (even `:443`), userinfo, non-https schemes, scheme-less values, trailing-dot hostnames, percent-encoding, backslashes, paths, queries, and fragments. A configured endpoint that is not the official one is treated as real-money capable and production-grade, so it can never earn development shortcuts or development approval authority. DNS is never consulted to establish trust.

There is no production gateway policy: a reviewed design would be needed before any non-official production endpoint could be supported. `RealDarajaClient` takes its endpoint only from this policy (there is no constructor override) and never follows HTTP redirects; a 3xx response is treated as a failed call.

### Daraja credential resolution

The client and the validator resolve credentials with the same function. The mode-specific variable (`DARAJA_SANDBOX_*` or `DARAJA_PRODUCTION_*`) always wins. The legacy unprefixed variables (`DARAJA_CONSUMER_KEY`, `DARAJA_CONSUMER_SECRET`, `DARAJA_PASSKEY`, `DARAJA_SHORTCODE`, `DARAJA_CALLBACK_URL`) are used as a fallback **only in contradiction-free local development**. In staging and production-grade deployments the client never falls back to them, and the validator rejects configurations that would need the fallback. Production mode never uses legacy credentials.

### Production-grade controls

When the tier is production-grade, startup is rejected unless all of these hold:

- `OPERATOR_AUTH_ENABLED=true`, `AUTH_MODE=oidc`, `OIDC_PROVIDER_MODE=jwks`, and an https `OIDC_ISSUER`.
- No shared static operator tokens (`OPERATOR_VIEWER_TOKEN`, `OPERATOR_APPROVER_TOKEN`, `OPERATOR_ADMIN_TOKEN`) are configured. There is no reviewed production design for shared static credentials.
- `STORAGE_MODE=postgres`, with a `DATABASE_URL` that does not use default development credentials.
- `RATE_LIMIT_ENABLED=true` and `RATE_LIMIT_MODE=redis`.
- `CALLBACK_REPLAY_PROTECTION_ENABLED=true` and `CALLBACK_REPLAY_MODE=redis`.
- `CALLBACK_SOURCE_VERIFICATION_MODE` is `trusted_proxy` or `strict_block`, never `development`.
- `CALLBACK_SHARED_SECRET` (and `TRUSTED_PROXY_SHARED_SECRET` in `trusted_proxy` mode) is at least 32 characters and not a placeholder.
- With `DARAJA_MODE=production`: every `DARAJA_PRODUCTION_*` credential is set and not a placeholder, the base URL is the official production endpoint, and `DARAJA_PRODUCTION_CALLBACK_URL` is a public callback destination (below).
- With `DARAJA_MODE=production`, the Transaction Status settings the client sends are required because the operation is always reachable (MCP tools and reconciliation): `DARAJA_INITIATOR_NAME` and `DARAJA_SECURITY_CREDENTIAL` must be set and not placeholders, and `DARAJA_TRANSACTION_STATUS_RESULT_URL` and `DARAJA_TRANSACTION_STATUS_TIMEOUT_URL` must be public callback destinations **on the same host as `DARAJA_PRODUCTION_CALLBACK_URL`**. That host is the single trusted callback ingress (the trusted proxy boundary); no other result destination is permitted.

A public callback destination is an https URL whose host passes the shared hostname policy (`is_acceptable_dns_hostname`) and is not a reserved or internal name. The policy is enforced in code, offline, without DNS lookups:

- The host must be a DNS name, never an address. Standard IPv4 and IPv6 literals are rejected, and so is every numeric notation resolvers may interpret as IPv4: leading-zero octets (`192.168.1.020`), octal (`0300.0251.0326.0310`), hexadecimal (`0x7f000001`), short-form (`1.2.3`, `127.1`), and integer (`2130706433`) forms. The top-level label must start with a letter, which rules out every numeric or hex final label while keeping Punycode domains (`xn--...`) valid.
- Two or more consecutive numeric or hex labels are rejected. This is a defence-in-depth heuristic against names that embed an address (for example `10.0.0.1.nip.io`), not a guarantee.
- Reserved or internal names (`localhost`, `.localhost`, `.local`, `.internal`, `.lan`, `.home.arpa`, `.corp`, `.test`, `.example`, `.invalid`), explicit ports, userinfo, fragments, trailing dots, surrounding whitespace, percent-encoding, backslashes, and non-ASCII characters are rejected.

**Syntax validation does not prove control of a domain.** The following remain operational requirements that code cannot verify:

- DNS ownership of the callback host, and that it does not resolve (directly or through wildcard DNS) to private, loopback, or link-local addresses.
- TLS certificate validity for the callback host.
- A secure public ingress that terminates TLS and forwards only callback paths.
- The trusted proxy boundary that injects `TRUSTED_PROXY_SHARED_SECRET`, so arbitrary public clients cannot.
- Access controls on callback endpoints at the ingress (rate limits and, where available, provider source restrictions).

### Durable approval blocker

`STORAGE_MODE=postgres` persists transactions and audit events only. **Approval requests are always process-local and in memory**, so no available configuration provides durable, shared approvals. Validation therefore reports `APPROVAL_PERSISTENCE` for every production-grade configuration, and **production-grade startup is currently impossible by design**. This blocker must not be weakened to make production start; it is removed only when durable, atomic approval persistence is implemented.

### Startup enforcement

The same gate (`app.startup.enforce_startup_safety`) runs before any request is served or any payment provider is built. Enforcement does not depend on the ASGI lifespan:

- **Validated container factory.** `AppContainer.from_environment()` (used by `create_app_container()`, the FastAPI accessor, and the MCP server) validates the configuration before constructing any repository, client, or payment provider. A container exists only if validation succeeded; there is no separate "validated" flag. Network dependency checks are not repeated here.
- **Request gate.** Middleware resolves the validated container before any route runs, including routes that never use it (the HTML operator UI, OpenAPI docs). If validation fails, every request receives a generic `503 service_unavailable` with no setting names. The rejection is cached for the process lifetime (settings cannot change without a restart), so it is logged once and not re-validated per request. This covers `uvicorn --lifespan off` and ASGI servers without lifespan support.
- **Request gate fast path.** Once a validated container exists and no dependency override is active, the middleware lets requests through without a threadpool hop. A container only exists after successful validation, so this never admits an unvalidated configuration; overrides, cached rejections, and first requests always take the full path.
- **FastAPI lifespan.** With the lifespan enabled (the default), configuration and dependency checks run before startup completes; on failure uvicorn reports "Application startup failed" and exits without accepting connections.
- **`scripts/start_app.py`**, before database migrations run.
- **MCP server.** `create_mcp_server()` without an injected container (the path used by `run()` and `scripts/run_mcp_server.py`) validates before building its container and registering tools.

Validation messages and the `startup_config_rejected` log event contain setting names only, never values. When the lifespan is disabled, logging is not configured, so the rejection is printed once as plain text by Python's last-resort handler. `AppContainer.mock(settings=...)` builds a container for explicit settings without validation; it is for tests and is not used by any serving path.

Other validation applies in every tier: mode settings must be recognized values; Redis-backed modes require a `REDIS_URL` that the Redis client can parse; Postgres storage requires a `DATABASE_URL` that SQLAlchemy can parse with an installed driver. Both are parsed offline with the same constructors the runtime uses, so a malformed URL is a deterministic configuration error (a cached `503` when serving without the lifespan) rather than a failure during container construction, and the URL, which can contain credentials, never appears in the error. Transient dependency outages are not configuration errors: they surface at request time as `503 infrastructure_unavailable` and are not cached. Unexpected programming errors remain `500` responses so they stay visible to operators. OIDC mode requires an issuer and audience, and token mode with auth enabled requires at least one token.

## Approval Authority

AI agents may request payments, but they must never authorize financial actions.

- No MCP tool approves or rejects approval requests. `approve_payment_request` and `reject_payment_request` are listed in `AGENT_FORBIDDEN_TOOLS` and are denied unconditionally; configuration cannot re-enable them, and the MCP server refuses to register them.
- Approve and reject decisions are only available through the operator approval API, and the reviewer is always the server-authenticated `OperatorIdentity`. Request bodies cannot supply reviewer identity, and there is no fallback or synthetic reviewer.
- Every identity records its `authentication_method` (`oidc_jwks`, `oidc_development`, `static_token`, `auth_disabled`) and `principal_type` (`human`, `application`, `unknown`). Only `oidc_jwks` with a delegated user token (`scp` claim, not `idtyp=app`) counts as a verified human.
- Outside development environments, only verified humans may approve or reject. App-only identities are always denied. OIDC JWKS identities must be verified humans in every environment: a cryptographically valid token classified `unknown` never receives approval authority. The development-only single-reviewer exception applies solely to `static_token`, `oidc_development`, and `auth_disabled` identities, and never when `DARAJA_MODE=production`. Multi-reviewer quorums count only verified humans in every environment, because shared tokens and development identities cannot prove distinct reviewers.
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

GitHub Actions runs `gitleaks/gitleaks-action@v2` (gitleaks 8.24.3, pinned) on every push and pull request. The action scans the commits in the pushed range or in the pull request, not the working tree, so the checkout uses full history (`fetch-depth: 0`).

The action accepts no `with:` inputs. It is configured through environment variables, and `GITLEAKS_CONFIG` points it at `.gitleaks.toml`. That file extends gitleaks' default rule set (`[extend] useDefault = true`) and allowlists only documented placeholders and test-only fake values. Scanner errors fail the job; they are never treated as a clean result.

Before this was corrected, `.gitleaks.toml` defined no rules and did not extend the defaults, so earlier CI secret scans ran with zero detection rules. Commits that only passed those scans have not been meaningfully scanned. A one-time full-history scan is still required before production credentials are handled.

## Rotation Expectations

Production launch should include a documented rotation process for:

- Daraja production credentials
- callback shared secret
- operator identity credentials or SSO client secrets
- database credentials
- Redis credentials

No production credential should be long-lived without an owner, expiry/rotation plan, and incident response path.
