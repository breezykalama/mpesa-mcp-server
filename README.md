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

- call the real Safaricom Daraja API
- persist transactions to a real database
- verify callback signatures or source IPs
- implement human approval workflows
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
        v
STK Callback Handler
app/callbacks/handlers.py
```

Business logic lives in services, handlers, policies, and generators. MCP and FastAPI layers are intentionally thin adapters.

## Available MCP Tools

| Tool | Purpose | Current behavior |
| --- | --- | --- |
| `initiate_stk_push` | Start an STK push request | Uses `MockDarajaClient`, saves a pending transaction in memory |
| `check_transaction_status` | Check a checkout request status | Uses mock Daraja status response and includes local transaction data when found |
| `generate_receipt` | Generate a receipt for a completed transaction | Generates an in-memory structured receipt only for completed transactions |
| `get_today_summary` | Show today's M-Pesa revenue summary | Counts in-memory transactions created today |
| `get_failed_transactions` | Show failed transactions | Returns in-memory transactions with `failed` status |

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

## Current Mock Mode

The MVP is intentionally mock-backed:

- Daraja calls are handled by `MockDarajaClient`
- transactions are stored in `InMemoryTransactionRepository`
- audit events are stored in `InMemoryAuditLogger`
- callback payloads can update in-memory transactions
- receipt generation returns structured data, not PDFs

This makes the system deterministic, fast to test, and safe to demo without live payment credentials.

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

DARAJA_CONSUMER_KEY=
DARAJA_CONSUMER_SECRET=
DARAJA_PASSKEY=
DARAJA_SHORTCODE=
DARAJA_CALLBACK_URL=

MAX_STK_AMOUNT=10000
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
   - transaction status query integration
   - HTTP error handling and retries

2. PostgreSQL persistence
   - SQLAlchemy models
   - Alembic migrations
   - repository implementation backed by PostgreSQL
   - durable audit trail

3. Callback verification
   - callback URL validation
   - source verification strategy
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
