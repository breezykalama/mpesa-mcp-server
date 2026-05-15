# M-Pesa MCP Server Demo Guide

This guide helps a recruiter, engineer, or reviewer understand what the project proves and how to run the local demo safely.

The demo uses mock Daraja behavior by default. It does not require real Safaricom credentials, real phone numbers, or live payments.

## What The Demo Proves

This project demonstrates a safety-first backend architecture for agent-assisted M-Pesa workflows.

It proves that an AI agent can interact with payment-related operations through controlled tools instead of direct access to payment APIs.

The demo shows:

- typed MCP tool inputs and structured responses
- policy checks before payment initiation
- approval workflow for risky payments
- idempotency to avoid duplicate payment initiation
- rate limiting for sensitive MCP tools
- M-Pesa callback handling with optional shared-secret validation
- structured audit events
- structured operational logs
- PostgreSQL and Redis-backed local runtime with Docker Compose
- automatic Alembic migrations during Docker startup
- health checks for local validation

This is an MVP architecture demo, not a production payment platform.

## Architecture Summary

```text
AI Agent / MCP Client
        |
        v
MCP Server
app/mcp/server.py
        |
        v
Thin Tool Wrappers
app/mcp/tools.py
        |
        v
Services
app/services/
        |
        +--> Safety Policy
        +--> Approval Workflow
        +--> Daraja Client Protocol
        +--> Transaction Repository
        +--> Audit Logger
        +--> Metrics / Logs

FastAPI
app/main.py
        |
        +--> /health
        +--> /health/ready
        +--> /metrics
        +--> /callbacks/mpesa/stk
```

The MCP layer stays thin. Business decisions live in services and policies. External systems are behind protocols or repositories so mocks can be replaced later.

## Run With Docker

Docker Compose starts:

- FastAPI app
- PostgreSQL 16
- Redis 7

Daraja stays in mock mode.

```bash
docker compose up --build
```

During startup, the app runs:

```bash
alembic upgrade head
```

Then it starts FastAPI on port `8000`.

## Verify Health

In another terminal:

```bash
curl http://localhost:8000/health
```

Expected simplified response:

```json
{
  "status": "ok",
  "storage_mode": "postgres"
}
```

Readiness check:

```bash
curl http://localhost:8000/health/ready
```

Expected simplified response:

```json
{
  "status": "ready",
  "ready": true,
  "storage_mode": "postgres"
}
```

Stop the stack:

```bash
docker compose down
```

To remove local demo database and Redis volumes:

```bash
docker compose down -v
```

## Run Tests

Install dependencies:

```bash
uv sync --group dev
```

Run tests:

```bash
uv run pytest
```

Run the same quality checks used in CI:

```bash
python -m compileall app tests scripts alembic
uv run pytest
uv run ruff check app tests scripts alembic
uv run mypy app tests scripts
```

The test suite is Docker-free and does not require PostgreSQL, Redis, Daraja credentials, or live network calls.

## Mock Mode

Mock mode is the safe default.

In mock mode:

- STK Push returns fake checkout and merchant request IDs
- transaction status returns a fake completed response
- no Daraja API call is made
- no payment is initiated
- tests remain deterministic

Docker Compose also defaults to:

```env
DARAJA_MODE=mock
```

This lets reviewers run the demo safely without credentials.

## Local MCP Smoke Script

For the fastest end-to-end demo, run the local smoke script:

```bash
uv run python scripts/smoke_mcp_tools.py
```

The script uses `AppContainer` with in-memory storage and `MockDarajaClient`. It calls the MCP tool wrapper functions directly and prints clean JSON for each step:

- safe `initiate_stk_push`
- `check_transaction_status`
- simulated STK callback
- `generate_receipt`
- `get_today_summary`
- above-limit `initiate_stk_push` returning `approval_required`
- `approve_payment_request` executing the approved payment once

No Docker, Redis, PostgreSQL, Daraja credentials, or live network calls are required.

## Sample MCP Tool Flows

The examples below show simplified MCP-style inputs and responses. IDs are dummy values.

### initiate_stk_push

Input:

```json
{
  "phone_number": "254700000000",
  "amount": 1000,
  "account_reference": "INV-DEMO-001",
  "description": "Demo invoice payment",
  "idempotency_key": "demo-payment-001"
}
```

Expected simplified response:

```json
{
  "status": "pending",
  "allowed": true,
  "reason": "STK push initiated successfully.",
  "data": {
    "transaction_id": "generated-transaction-id",
    "checkout_request_id": "ws_CO_generated",
    "merchant_request_id": "mock_generated",
    "idempotency_key": "demo-payment-001"
  }
}
```

What this demonstrates:

- input validation
- policy check
- mock Daraja client
- pending transaction persistence
- audit event
- structured operational log
- idempotency key support

### check_transaction_status

Input:

```json
{
  "checkout_request_id": "ws_CO_generated"
}
```

Expected simplified response:

```json
{
  "status": "completed",
  "allowed": true,
  "reason": "The service request is processed successfully.",
  "data": {
    "checkout_request_id": "ws_CO_generated",
    "result_code": "0",
    "result_description": "The service request is processed successfully."
  }
}
```

What this demonstrates:

- read-only policy
- local transaction lookup
- Daraja client protocol
- audit event for status checks

### approve_payment_request

If an STK Push amount exceeds the configured maximum, the payment is not sent immediately.

High-value input:

```json
{
  "phone_number": "254700000000",
  "amount": 25000,
  "account_reference": "INV-DEMO-002",
  "description": "High-value demo invoice",
  "idempotency_key": "demo-payment-002"
}
```

Expected simplified response:

```json
{
  "status": "approval_required",
  "allowed": false,
  "requires_approval": true,
  "reason": "Amount exceeds maximum allowed STK push amount.",
  "data": {
    "approval_id": "generated-approval-id"
  }
}
```

Approval input:

```json
{
  "approval_id": "generated-approval-id"
}
```

Expected simplified response:

```json
{
  "status": "approved",
  "allowed": true,
  "reason": "Approval request approved and payment execution attempted.",
  "data": {
    "approval": {
      "approval_id": "generated-approval-id",
      "status": "approved"
    },
    "payment": {
      "status": "pending",
      "transaction_id": "generated-transaction-id"
    }
  }
}
```

What this demonstrates:

- high-value payment protection
- approval queue
- no Daraja call before approval
- execution exactly once after approval
- idempotency reuse

### generate_receipt

Receipt generation is only allowed for completed transactions.

Input:

```json
{
  "checkout_request_id": "ws_CO_completed"
}
```

Expected simplified response:

```json
{
  "status": "ok",
  "allowed": true,
  "reason": "Receipt generated successfully.",
  "data": {
    "receipt": {
      "receipt_id": "generated-receipt-id",
      "checkout_request_id": "ws_CO_completed",
      "amount": 1000,
      "status": "completed",
      "issued_at": "2026-01-01T00:00:00Z"
    }
  }
}
```

What this demonstrates:

- receipt rules
- completed-only guard
- structured receipt output
- audit event

### get_today_summary

Input:

```json
{}
```

Expected simplified response:

```json
{
  "status": "ok",
  "allowed": true,
  "reason": "Today's summary retrieved successfully.",
  "data": {
    "summary": {
      "total_transactions": 3,
      "completed_transactions": 1,
      "failed_transactions": 1,
      "pending_transactions": 1,
      "total_revenue": 1000
    }
  }
}
```

What this demonstrates:

- simple business analytics
- revenue counts completed transactions only
- repository-backed summaries

## Callback Demo

The callback route is:

```text
POST /callbacks/mpesa/stk
```

If `CALLBACK_SHARED_SECRET` is configured, requests must include:

```text
X-Callback-Secret: configured-demo-secret
```

Missing or invalid secrets return:

```json
{
  "detail": "Invalid callback credentials."
}
```

This demonstrates callback validation without storing or exposing real secrets.

## Safety Features Demonstrated

### Approval Workflow

Payments above `MAX_STK_AMOUNT` are converted into approval requests. They are not executed immediately.

### Idempotency

Repeated STK Push requests with the same idempotency key return the existing transaction instead of creating duplicates.

### Rate Limiting

Sensitive MCP tools are rate-limited:

- `initiate_stk_push`
- `check_transaction_status`
- `approve_payment_request`
- `reject_payment_request`

When exceeded, the tool returns:

```json
{
  "status": "rate_limited",
  "allowed": false,
  "reason": "Rate limit exceeded"
}
```

### Callback Secret Validation

The callback route can require `X-Callback-Secret`. Rejected attempts are logged as audit events.

### Audit Logging

Audit events are separate from operational logs and are intended for durable business/security tracking.

### Structured Logs

Operational logs are JSON by default and include:

- UTC timestamp
- level
- logger name
- message
- event type
- exception info when present

Sensitive credentials are not logged.

### Health Checks

The app exposes:

- `GET /health`
- `GET /health/ready`
- `GET /metrics`

Docker Compose also has service health checks for the app, PostgreSQL, and Redis.

## What This Demo Does Not Do

The demo does not:

- initiate real M-Pesa payments
- require live Daraja credentials
- replace Safaricom production approval/compliance processes
- provide a dashboard UI
- generate PDF receipts

It is a backend architecture MVP for safe AI-agent payment workflows.
