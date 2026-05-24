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

