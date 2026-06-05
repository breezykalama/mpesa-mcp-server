# M-Pesa MCP Server

[![CI](https://github.com/breezykalama/mpesa-mcp-server/actions/workflows/ci.yml/badge.svg)](https://github.com/breezykalama/mpesa-mcp-server/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-ready-009688)
![MCP](https://img.shields.io/badge/MCP-server-6f42c1)
![Docker](https://img.shields.io/badge/Docker-compose-2496ed)
![Tests](https://img.shields.io/badge/tests-passing-brightgreen)

An experimental Model Context Protocol (MCP) server that lets an AI agent interact with guarded payment workflows through typed tools.

The project is a production-shaped prototype for safe agent-assisted payments. It demonstrates how an agent can request payment actions, check transaction state, handle STK callbacks, generate receipts, ask revenue questions, and support operator approvals while payment execution remains isolated behind service interfaces and provider adapters.

## Current Status

- Mock mode complete for safe local demos
- Daraja sandbox STK Push supported
- Daraja sandbox Transaction Status supported
- Mock Airtel Money provider demonstrates multi-rail architecture
- PostgreSQL persistence supported
- Redis-backed rate limiting supported
- Reconciliation engine supported
- Operator dashboard API supported
- Lightweight operator auth/RBAC supported
- React operator dashboard supported for demos
- CI is green on GitHub Actions

## Quick Demo

```bash
uv sync
uv run pytest
uv run python scripts/smoke_mcp_tools.py
docker compose up --build
```

The smoke script runs fully in memory and now demonstrates the legacy M-Pesa flow, generic Daraja-backed payment tools, and the mock Airtel Money rail. Docker Compose starts FastAPI, PostgreSQL, and Redis in mock payment mode.

## Project Purpose

This project is a backend architecture prototype for agent-assisted payment operations, starting with M-Pesa. It is designed to answer questions like:

- "Initiate an STK push for this invoice."
- "Check the status of this checkout request."
- "Generate a receipt for this completed payment."
- "Show today's M-Pesa revenue."
- "Show today's failed transactions."

The emphasis is on safe tool boundaries, testable service layers, and replaceable infrastructure. Mock mode remains the default for local demos and CI.

Daraja/M-Pesa is the first real payment rail. The project also includes a mock Airtel Money provider to demonstrate that the service layer can support additional rails without changing the MCP tool API.

## Not A Payment Platform

This repository is not a production payment platform yet.

It does not currently:

- enable production Safaricom Daraja mode
- cryptographically verify callback signatures or source IPs
- provide enterprise-grade approval controls such as SSO, policy delegation, or reviewer groups
- provide merchant settlement, refunds, chargebacks, or compliance workflows
- generate legal/tax-compliant PDF receipts

Instead, it is a controlled MCP-first backend and operator console that shows how those concerns can be introduced behind explicit interfaces.

## Architecture

```text
AI Agent / MCP Client
        |
        v
FastMCP Server
app/mcp/server.py
        |
        v
Thin MCP Tool Wrappers
app/mcp/tools.py
        |
        v
Service Layer
app/services/
  - PaymentService
  - TransactionService
  - ReceiptService
  - AnalyticsService
  - ReconciliationService
        |
        +-------------------+
        |                   |
        v                   v
Safety Policy          Domain Utilities
app/safety/            app/receipts/
  - PaymentPolicy        - ReceiptGenerator
        |
        v
Interfaces / Adapters
app/payments/   app/storage/       app/audit/
  - Daraja rail   - In-memory repo   - Structured audit repo
  - Airtel mock   - PostgreSQL repo

FastAPI Operator Routes
app/operator/routes.py
app/approvals/routes.py
        |
        +--> Bearer token auth + RBAC
        |
        v
Operator Dashboard / Approval API

FastAPI Callback Route
app/callbacks/routes.py
        |
        +--> Optional shared-secret validation
        |
        v
STK Callback Handler
app/callbacks/handlers.py
```

Business logic lives in services, handlers, policies, and generators. MCP and FastAPI layers are intentionally thin adapters.

## Available MCP Tools

| Tool | Purpose | Current behavior |
| --- | --- | --- |
| `initiate_payment` | Start a provider-agnostic payment request | Uses the configured `PaymentProviderProtocol` implementation |
| `initiate_stk_push` | Start a legacy M-Pesa STK Push request | Preserved for M-Pesa compatibility and routed through the same payment service |
| `check_payment_status` | Check provider transaction status | Uses the configured payment provider |
| `check_transaction_status` | Check a legacy M-Pesa transaction reference | Uses mock mode by default; sandbox mode can submit a Daraja Transaction Status query |
| `generate_receipt` | Generate a receipt for a completed transaction | Generates structured receipt data only for completed transactions |
| `get_today_summary` | Show today's revenue summary | Counts transactions from the configured repository and only includes completed transactions in revenue |
| `get_failed_transactions` | Show failed transactions | Returns transactions with `failed` status from the configured repository |
| `approve_payment_request` | Approve a pending risky payment request | Marks approval as approved and executes the original STK push once |
| `reject_payment_request` | Reject a pending risky payment request | Marks approval as rejected without initiating Daraja |
| `run_reconciliation` | Detect local/provider transaction mismatches | Read-only reconciliation summary with detailed findings |

## Safety Rules

The `PaymentPolicy` enforces the first safety boundary:

- Read-only actions are allowed:
  - `check_transaction_status`
  - `get_today_transactions`
  - `get_failed_transactions`
  - `generate_receipt`
- `initiate_stk_push` requires:
  - `amount`
  - `phone_number`
  - amount greater than `0`
- STK push amounts above `MAX_STK_AMOUNT` return `approval_required`
- Unknown actions are blocked

The default maximum STK amount is `10000`.

## Safety & Governance

This project treats payment-capable AI tools as controlled operations, not open-ended API access.

Key safeguards include:

- **Tool governance:** operators can enable, block, or require approval for MCP tools with `ENABLED_MCP_TOOLS`, `BLOCKED_MCP_TOOLS`, and `APPROVAL_REQUIRED_MCP_TOOLS`.
- **Payment policy:** STK Push requests require amount and phone number validation, block invalid amounts, and route above-limit payments into approval.
- **Approval workflow:** risky STK Push requests create approval records and are not sent to Daraja until explicitly approved.
- **Approval expiry:** pending approvals expire after `APPROVAL_EXPIRY_MINUTES` and cannot be approved or executed after expiry.
- **Multi-reviewer approval:** high-risk approvals can require multiple distinct operator reviews before payment execution.
- **Idempotency:** repeated STK Push requests with the same idempotency key return the existing transaction instead of initiating a duplicate.
- **Transaction state machine:** pending transactions can move only into terminal states, and terminal states cannot be overwritten by duplicate or late callbacks.
- **Database integrity:** PostgreSQL models and migrations enforce critical transaction uniqueness and allowed status values.
- **Infrastructure failure policy:** configured Postgres and Redis dependencies are checked at startup, and Redis-backed safety controls fail closed at runtime.
- **Rate limiting:** sensitive MCP tools can be limited in memory or Redis.
- **Callback security:** callbacks can require `X-Callback-Secret`, and duplicate callback payloads are rejected with replay protection.
- **Callback trust validation:** callbacks must match a known transaction and any supplied amount or phone metadata must match the original request.
- **Audit trail:** payment initiation, callbacks, approvals, receipt generation, and rejected security events are captured as structured audit events.
- **Correlation IDs:** FastAPI requests and MCP tool execution carry correlation IDs through logs and audit events for traceability.
- **Structured logs:** operational logs are JSON by default and avoid known secret fields.

These controls are intentionally modular. Local development can run fully in memory, while Docker Compose can run the backend with PostgreSQL and Redis. Daraja sandbox support is available behind the same provider abstraction.

## Operator Security

Operator and approval HTTP endpoints are protected with lightweight bearer-token authentication and role-based access control.

Roles:

- `viewer`: can read operator dashboard endpoints
- `approver`: can read operator endpoints and approve or reject payment approvals
- `admin`: can access everything, including reconciliation runs

Protected endpoints:

- `GET /operator/transactions` requires `viewer+`
- `GET /operator/transactions/{transaction_id}` requires `viewer+`
- `GET /operator/audit-events` requires `viewer+`
- `GET /operator/analytics/today` requires `viewer+`
- `POST /operator/reconciliation/run` requires `admin`
- `GET /operator/ui` serves the minimal browser console
- `GET /approvals/pending` requires `approver+`
- `GET /approvals/{approval_id}` requires `approver+`
- `POST /approvals/{approval_id}/approve` requires `approver+`
- `POST /approvals/{approval_id}/reject` requires `approver+`
- `POST /approvals/expire-stale` requires `approver+`

Configure local tokens with:

```env
OPERATOR_AUTH_ENABLED=false
OPERATOR_VIEWER_TOKEN=
OPERATOR_APPROVER_TOKEN=
OPERATOR_ADMIN_TOKEN=

APPROVAL_EXPIRY_MINUTES=30
APPROVAL_REQUIRED_REVIEWERS=1
HIGH_RISK_APPROVAL_REQUIRED_REVIEWERS=2
HIGH_RISK_AMOUNT_THRESHOLD=50000
```

Requests use:

```text
Authorization: Bearer <operator-token>
```

When `OPERATOR_AUTH_ENABLED=false`, local development access uses a synthetic admin principal. Raw tokens are never logged.

## Operator Console UI

The project includes two operator UI options.

The polished demo dashboard lives in `frontend/` and uses Vite, React, TypeScript, Tailwind CSS, Axios, and TanStack Query.

Run the backend:

```bash
uv run python scripts/start_app.py
```

Run the frontend:

```bash
cd frontend
npm ci
npm run dev
```

Open:

```text
http://localhost:5173
```

The dashboard reads `VITE_API_BASE_URL`, defaulting to `http://localhost:8000`. Operators paste a bearer token into the login screen; the token is stored only in browser `localStorage` and sent as `Authorization: Bearer <token>`.

The dashboard includes:

- login/token screen
- today's analytics cards
- recent transactions table
- pending approvals with approve/reject actions
- recent audit events
- reconciliation panel
- system status panel

The FastAPI app still serves the original minimal HTML fallback at:

```text
GET /operator/ui
```

Both UIs are demo consoles for reviewing the backend workflow. The React dashboard is production-minded, but it is still a project dashboard rather than a fully managed enterprise frontend.

## Callback Security

The callback route supports an optional shared-secret guard for development and sandbox deployments:

- Set `CALLBACK_SHARED_SECRET` in the environment to require callback authentication.
- Send the same value in the `X-Callback-Secret` request header.
- Missing or invalid secrets are rejected with `401`.
- Rejected callback attempts are written to the audit log as `stk_callback_rejected`.
- Duplicate callback payloads are rejected with `409` as `duplicate_callback`.
- Malformed callbacks are rejected as `invalid_callback`.
- Unknown transaction callbacks are rejected as `unknown_transaction`.
- Supplied callback amount and phone metadata must match the stored transaction.
- If `CALLBACK_SHARED_SECRET` is empty, callbacks are accepted for local mock development.

`CALLBACK_SOURCE_VERIFICATION_MODE=development` intentionally allows local callbacks and is unsafe for production. `strict_block` rejects all callbacks. `trusted_proxy` requires a trusted reverse proxy, API gateway, ingress controller, or edge worker to inject the configured trusted proxy header before the app accepts the callback.

This is a pragmatic prototype control, not a complete production verification strategy. A production adapter should add source validation, stronger payload integrity checks, and provider-specific verification when available.

## Local Mock Mode

Mock mode remains available for fast local development, CI, and safe demos:

- Daraja calls are handled by `MockDarajaClient`
- transactions are stored in `InMemoryTransactionRepository`
- audit events are stored in `InMemoryAuditLogger`
- callback payloads can update in-memory transactions
- receipt generation returns structured data, not PDFs

This makes the system deterministic, fast to test, and safe to demo without live payment credentials. Outside mock mode, the project also includes Daraja sandbox, PostgreSQL, Redis, and provider-aware transaction storage adapters.

## Daraja Sandbox And Production-Mode Hardening

`RealDarajaClient` supports sandbox OAuth token retrieval, STK Push initiation, and Transaction Status query submission.

The client is also production-mode capable behind `DARAJA_MODE=production`, but production use still requires Safaricom production onboarding, real production credentials, callback validation, and a controlled low-value real-money validation before launch. No production credentials should ever be committed.

Daraja hardening includes:

- separate sandbox and production base URLs
- separate sandbox and production credential variables with backwards-compatible fallbacks
- configured HTTP timeouts
- retry support for OAuth and transaction status transient failures
- conservative STK Push retry behavior; STK Push is not retried by the Daraja client unless the platform can prove the request is safe through idempotency context
- a small circuit breaker for provider outages
- normalized provider error categories such as `auth_error`, `validation_error`, `timeout`, `rate_limited`, and `provider_unavailable`

For transaction status, Daraja expects an M-Pesa transaction ID or another suitable Daraja transaction reference. The public project method remains `check_transaction_status(checkout_request_id)` for compatibility, but sandbox status checks should be called with the correct Daraja transaction reference once real transaction IDs are available.

## Multi-Rail Provider Abstraction

Payment execution goes through a generic `PaymentProviderProtocol`. The current real adapter is `DarajaPaymentProvider`, backed by the Daraja client.

The project also includes `AirtelMoneyMockProvider`, selected with:

```env
PAYMENT_PROVIDER=airtel_mock
```

This mock provider does not call Airtel or require credentials. It exists to prove that transactions can be stored with provider-aware metadata such as `provider="airtel"` and `rail="airtel_money"` while preserving the current MCP tool contract.

The MCP server now exposes both legacy M-Pesa-specific tools and provider-agnostic tools:

- Legacy compatibility:
  - `initiate_stk_push`
  - `check_transaction_status`
- Generic multi-rail tools:
  - `initiate_payment`
  - `check_payment_status`

The generic tools call the same service layer and honor the same policy, rate limit, approval, idempotency, audit, and provider configuration controls.

## Setup

Requirements:

- Python 3.12
- `uv`

Install dependencies:

```bash
uv sync --group dev
```

Create a local environment file:

```bash
cp .env.example .env
```

The current `.env.example` includes:

```env
APP_ENV=development
DATABASE_URL=postgresql+asyncpg://mpesa:mpesa@localhost:5432/mpesa_mcp
STORAGE_MODE=memory

DARAJA_MODE=mock
DARAJA_SANDBOX_BASE_URL=https://sandbox.safaricom.co.ke
DARAJA_PRODUCTION_BASE_URL=https://api.safaricom.co.ke
DARAJA_CONSUMER_KEY=
DARAJA_CONSUMER_SECRET=
DARAJA_PASSKEY=
DARAJA_SHORTCODE=
DARAJA_CALLBACK_URL=
DARAJA_SANDBOX_CONSUMER_KEY=
DARAJA_SANDBOX_CONSUMER_SECRET=
DARAJA_SANDBOX_PASSKEY=
DARAJA_SANDBOX_SHORTCODE=
DARAJA_SANDBOX_CALLBACK_URL=
DARAJA_PRODUCTION_CONSUMER_KEY=
DARAJA_PRODUCTION_CONSUMER_SECRET=
DARAJA_PRODUCTION_PASSKEY=
DARAJA_PRODUCTION_SHORTCODE=
DARAJA_PRODUCTION_CALLBACK_URL=
DARAJA_INITIATOR_NAME=
DARAJA_SECURITY_CREDENTIAL=
DARAJA_TRANSACTION_STATUS_RESULT_URL=
DARAJA_TRANSACTION_STATUS_TIMEOUT_URL=
DARAJA_IDENTIFIER_TYPE=4
DARAJA_TRANSACTION_STATUS_REMARKS=Transaction status query
DARAJA_TRANSACTION_STATUS_OCCASION=Mpesa MCP status check
DARAJA_REQUEST_TIMEOUT_SECONDS=10
DARAJA_MAX_RETRIES=2
DARAJA_RETRY_BACKOFF_SECONDS=0.5
DARAJA_CIRCUIT_BREAKER_ENABLED=true
DARAJA_CIRCUIT_BREAKER_FAILURE_THRESHOLD=5
DARAJA_CIRCUIT_BREAKER_RECOVERY_SECONDS=60
CALLBACK_SHARED_SECRET=
CALLBACK_SOURCE_VERIFICATION_MODE=development
TRUSTED_PROXY_SHARED_SECRET=
TRUSTED_PROXY_HEADER_NAME=X-Trusted-Callback-Proxy

MAX_STK_AMOUNT=10000

RATE_LIMIT_ENABLED=true
RATE_LIMIT_MODE=memory
RATE_LIMIT_WINDOW_SECONDS=60
RATE_LIMIT_MAX_STK_PUSH=5
RATE_LIMIT_MAX_APPROVAL_ACTIONS=10
RATE_LIMIT_MAX_STATUS_CHECKS=30

REDIS_URL=redis://localhost:6379/0

OPERATOR_AUTH_ENABLED=true
OPERATOR_VIEWER_TOKEN=
OPERATOR_APPROVER_TOKEN=
OPERATOR_ADMIN_TOKEN=
```

## Running Tests

```bash
uv run pytest
```

Quality checks:

```bash
uv run ruff check app tests scripts
uv run mypy app tests scripts
python -m compileall app tests scripts
```

## Continuous Integration

GitHub Actions runs on every push and pull request.

The backend job uses Python 3.12 and `uv` to install dependencies, compile modules, run the pytest suite, run Ruff, and run mypy.

The frontend job uses Node.js LTS, installs dependencies with `npm ci`, runs Vitest, and runs the React dashboard production build.

The workflow does not start PostgreSQL, Redis, or call Daraja. Tests use in-memory adapters and mocked HTTP clients, so CI does not require production credentials or repository secrets.

## Docker Runtime

The default Docker runtime starts the FastAPI app with PostgreSQL and Redis for local validation. Daraja remains in mock mode, so no Safaricom credentials are required.

For a guided walkthrough of the demo, see [docs/demo-guide.md](docs/demo-guide.md).

For the prioritized path from prototype to production, see [docs/production-readiness-checklist.md](docs/production-readiness-checklist.md).

For the controlled first real-money validation process, see [docs/production-validation-runbook.md](docs/production-validation-runbook.md).

For secret handling, startup validation, and CI secret scanning expectations, see [docs/security-model.md](docs/security-model.md).

For token and future OIDC operator identity architecture, see [docs/operator-identity.md](docs/operator-identity.md).

For Microsoft Entra ID setup using the standards-based OIDC/JWKS implementation, see [docs/entra-id-setup.md](docs/entra-id-setup.md).

For non-production validation against a real Entra tenant, see [docs/entra-validation-runbook.md](docs/entra-validation-runbook.md).

```bash
docker compose up --build
```

When `STORAGE_MODE=postgres`, the app container runs `alembic upgrade head` before starting FastAPI. Migration failures stop startup so schema issues are visible immediately.

In another terminal, check the app health endpoint:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{
  "status": "ok",
  "storage_mode": "postgres"
}
```

The app service uses `STORAGE_MODE=postgres`, `RATE_LIMIT_MODE=redis`, and `DARAJA_MODE=mock` by default. The MCP server remains optional and can still be started separately with `uv run python scripts/run_mcp_server.py`.

To open the demo operator console:

```text
http://localhost:8000/operator/ui
```

Paste a configured operator token into the token field before loading protected data.

To run the React operator dashboard instead:

```bash
cd frontend
cp .env.example .env
npm ci
npm run dev
```

Then open:

```text
http://localhost:5173
```

## Running The MCP Server

```bash
uv run python scripts/run_mcp_server.py
```

The server is created with `FastMCP` and registers the existing tool wrappers. It uses mock dependencies by default, so it is safe to run locally without Daraja credentials.

## Local MCP Smoke Demo

To see the tool flow without Docker, credentials, or a running MCP client, run:

```bash
uv run python scripts/smoke_mcp_tools.py
```

The script uses `AppContainer` in mock mode and calls the MCP tool wrappers directly. It demonstrates:

- legacy `initiate_stk_push` and `check_transaction_status`
- generic `initiate_payment` and `check_payment_status` with Daraja
- generic `initiate_payment` and `check_payment_status` with `PAYMENT_PROVIDER=airtel_mock`
- provider metadata: `provider`, `rail`, `provider_transaction_id`, `provider_reference`
- callback simulation, receipt generation, analytics, approval workflow, and reconciliation

## Callback Route

The FastAPI app includes a thin STK callback route:

```text
POST /callbacks/mpesa/stk
```

The route delegates to `StkCallbackHandler`, which parses the callback payload, updates local transaction state, and writes an audit event.

When `CALLBACK_SHARED_SECRET` is configured, requests must include:

```text
X-Callback-Secret: <configured shared secret>
```

## Example Tool Flows

### Initiate STK Push

```text
Agent calls initiate_stk_push
  -> MCP wrapper validates input
  -> PaymentService runs PaymentPolicy
  -> MockDarajaClient returns fake checkout IDs
  -> repository saves pending transaction
  -> audit event is written
  -> tool returns pending response
```

### Process STK Callback

```text
M-Pesa callback payload hits POST /callbacks/mpesa/stk
  -> route checks X-Callback-Secret if CALLBACK_SHARED_SECRET is set
  -> route delegates to StkCallbackHandler
  -> handler parses CheckoutRequestID, ResultCode, receipt metadata
  -> repository marks transaction completed or failed
  -> audit event is written
```

### Generate Receipt

```text
Agent calls generate_receipt
  -> MCP wrapper validates checkout_request_id
  -> ReceiptService fetches local transaction
  -> ReceiptGenerator allows only completed transactions
  -> audit event is written
  -> structured receipt is returned
```

### Ask For Today's Revenue

```text
Agent calls get_today_summary
  -> AnalyticsService reads today's transactions from the configured repository
  -> completed, failed, and pending counts are calculated
  -> total_revenue counts completed transactions only
```

## Roadmap

### Completed

**Agent and MCP layer**

- MCP runtime and tool registration
- legacy M-Pesa-specific tools
- generic multi-rail payment tools
- MCP wrappers for payment initiation, status checks, receipts, analytics, approvals, and reconciliation

**Payment provider layer**

- real Daraja sandbox adapter
- Daraja OAuth token handling
- STK Push sandbox request submission
- Daraja transaction status query submission
- Daraja production-mode configuration, timeouts, retries, circuit breaker, and normalized errors
- Airtel Money mock provider to prove the multi-rail architecture

**Persistence and infrastructure**

- PostgreSQL persistence
- SQLAlchemy transaction and audit models
- Alembic migrations
- durable audit trail
- Docker runtime with PostgreSQL and Redis
- automatic Alembic migrations on Docker startup
- GitHub Actions backend and frontend CI

**Safety and governance**

- payment safety policy
- callback shared secret validation
- callback replay protection
- Redis-backed MCP tool rate limiting
- idempotency for payment initiation
- approval workflow
- approval expiry
- stale approval handling
- multi-reviewer approvals
- high-risk approval thresholds
- operator RBAC

**Operator experience**

- React operator dashboard
- transaction monitoring
- receipt lookup and JSON export
- audit and callback timelines
- filtering, search, and sorting for transactions
- daily revenue and failure summaries
- approval review progress

**Observability and operations**

- health and readiness endpoints
- in-memory metrics endpoint
- structured application logs
- correlation ID tracing
- reconciliation engine
- Docker Compose local runtime

### Next Roadmap

- dynamic risk scoring
- configurable limits per environment or merchant
- provider/source verification strategy
- payload integrity checks
- controlled low-value Daraja production validation
- SSO/OAuth-backed operator identity
- receipt PDF export
- more payment rails beyond Daraja and Airtel mock

## Development Status

The project now has tested backend and frontend vertical slices for agent-facing MCP tools, payment workflows, callback handling, approval governance, operator APIs, and the React operator dashboard.

Daraja sandbox and PostgreSQL adapters exist. Mock mode remains available for local development, demos, and tests, so contributors can work without live credentials or real payment calls.

CI validates backend tests, linting, typing, and frontend tests/builds. Docker Compose can run the backend with PostgreSQL and Redis for local runtime validation.
