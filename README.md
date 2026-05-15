# M-Pesa MCP Server MVP

An experimental Model Context Protocol (MCP) server that lets an AI agent interact with a guarded M-Pesa workflow through typed tools.

The MVP demonstrates how an agent can request payment actions, check transaction state, handle STK callbacks, generate receipts, and ask simple revenue questions while the actual payment logic remains isolated behind service interfaces.

## Project Purpose

This project is a backend architecture MVP for agent-assisted M-Pesa operations. It is designed to answer questions like:

- "Initiate an STK push for this invoice."
- "Check the status of this checkout request."
- "Generate a receipt for this completed payment."
- "Show today's M-Pesa revenue."
- "Show today's failed transactions."

The emphasis is on safe tool boundaries, testable service layers, and replaceable infrastructure. The current implementation runs entirely in mock mode.

## Not A Payment Platform

This repository is not a production payment platform yet.

It does not currently:

- enable production Safaricom Daraja mode
- persist transactions to a real database
- cryptographically verify callback signatures or source IPs
- execute a full human approval workflow with identity, expiry, and reviewer controls
- provide merchant settlement, reconciliation, refunds, chargebacks, or compliance workflows
- generate legal/tax-compliant PDF receipts

Instead, it is a controlled MCP-first backend skeleton that shows how those concerns can be introduced behind explicit interfaces.

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
app/daraja/     app/storage/       app/audit/
  - Mock client   - In-memory repo   - In-memory audit log

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
| `initiate_stk_push` | Start an STK push request | Uses `MockDarajaClient`, saves a pending transaction in memory |
| `check_transaction_status` | Check a transaction reference status | Uses mock mode by default; sandbox mode can submit a Daraja Transaction Status query |
| `generate_receipt` | Generate a receipt for a completed transaction | Generates an in-memory structured receipt only for completed transactions |
| `get_today_summary` | Show today's M-Pesa revenue summary | Counts in-memory transactions created today |
| `get_failed_transactions` | Show failed transactions | Returns in-memory transactions with `failed` status |
| `approve_payment_request` | Approve a pending risky payment request | Marks approval as approved and executes the original STK push once |
| `reject_payment_request` | Reject a pending risky payment request | Marks approval as rejected without initiating Daraja |

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

## Callback Security

The callback route supports an optional shared-secret guard for development and sandbox deployments:

- Set `CALLBACK_SHARED_SECRET` in the environment to require callback authentication.
- Send the same value in the `X-Callback-Secret` request header.
- Missing or invalid secrets are rejected with `401`.
- Rejected callback attempts are written to the audit log as `stk_callback_rejected`.
- If `CALLBACK_SHARED_SECRET` is empty, callbacks are accepted for local mock development.

This is a pragmatic MVP control, not a complete production verification strategy. A production adapter should add source validation, replay protection, payload integrity checks, and provider-specific verification when available.

## Current Mock Mode

The MVP is intentionally mock-backed:

- Daraja calls are handled by `MockDarajaClient`
- transactions are stored in `InMemoryTransactionRepository`
- audit events are stored in `InMemoryAuditLogger`
- callback payloads can update in-memory transactions
- receipt generation returns structured data, not PDFs

This makes the system deterministic, fast to test, and safe to demo without live payment credentials.

## Daraja Sandbox Support

`RealDarajaClient` supports sandbox OAuth token retrieval, STK Push initiation, and Transaction Status query submission. Production mode is intentionally not enabled.

For transaction status, Daraja expects an M-Pesa transaction ID or another suitable Daraja transaction reference. The public project method remains `check_transaction_status(checkout_request_id)` for compatibility, but sandbox status checks should be called with the correct Daraja transaction reference once real transaction IDs are available.

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
DARAJA_CONSUMER_KEY=
DARAJA_CONSUMER_SECRET=
DARAJA_PASSKEY=
DARAJA_SHORTCODE=
DARAJA_CALLBACK_URL=
DARAJA_INITIATOR_NAME=
DARAJA_SECURITY_CREDENTIAL=
DARAJA_TRANSACTION_STATUS_RESULT_URL=
DARAJA_TRANSACTION_STATUS_TIMEOUT_URL=
DARAJA_IDENTIFIER_TYPE=4
DARAJA_TRANSACTION_STATUS_REMARKS=Transaction status query
DARAJA_TRANSACTION_STATUS_OCCASION=Mpesa MCP status check
CALLBACK_SHARED_SECRET=

MAX_STK_AMOUNT=10000

RATE_LIMIT_ENABLED=true
RATE_LIMIT_MODE=memory
RATE_LIMIT_WINDOW_SECONDS=60
RATE_LIMIT_MAX_STK_PUSH=5
RATE_LIMIT_MAX_APPROVAL_ACTIONS=10
RATE_LIMIT_MAX_STATUS_CHECKS=30

REDIS_URL=redis://localhost:6379/0
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

GitHub Actions runs on every push and pull request. The CI workflow uses Python 3.12 and `uv` to install dependencies, compile modules, run the pytest suite, run Ruff, and run mypy.

The workflow does not start PostgreSQL, Redis, or call Daraja. Tests use in-memory adapters and mocked HTTP clients, so CI does not require production credentials or repository secrets.

## Docker Runtime

The default Docker runtime starts the FastAPI app with PostgreSQL and Redis for local validation. Daraja remains in mock mode, so no Safaricom credentials are required.

For a guided walkthrough of the demo, see [docs/demo-guide.md](docs/demo-guide.md).

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

## Running The MCP Server

```bash
uv run python scripts/run_mcp_server.py
```

The server is created with `FastMCP` and registers the existing tool wrappers. It uses mock dependencies by default, so it is safe to run locally without Daraja credentials.

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
  -> AnalyticsService reads today's in-memory transactions
  -> completed, failed, and pending counts are calculated
  -> total_revenue counts completed transactions only
```

## Roadmap

1. Real Daraja sandbox adapter
   - OAuth token management
   - STK push request signing
   - transaction status query submission
   - HTTP error handling
   - retries and production hardening

2. PostgreSQL persistence
   - SQLAlchemy models
   - Alembic migrations
   - repository implementation backed by PostgreSQL
   - durable audit trail

3. Callback verification
   - callback URL validation
   - provider/source verification strategy
   - replay protection
   - payload integrity checks

4. Approval workflow
   - approval-required queue
   - reviewer identity and audit trail
   - configurable limits per environment or merchant
   - explicit release/deny actions

5. Dashboard
   - transaction monitoring
   - callback event timeline
   - daily revenue and failure summaries
   - receipt lookup and export

## Development Status

The MVP currently has tested vertical slices for:

- STK push initiation
- transaction status checks
- STK callback handling
- receipt generation
- daily analytics
- MCP server tool registration

All implemented behavior is covered by unit or integration tests and remains mock-backed until real Daraja and database adapters are introduced.
