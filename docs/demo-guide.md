# M-Pesa MCP Server Demo Guide

This guide helps a recruiter, engineer, or reviewer understand what the project proves and how to run the local demo safely.

The demo uses mock Daraja behavior by default. It does not require real Safaricom credentials, real phone numbers, or live payments.

Daraja/M-Pesa is the first real rail. A mock Airtel Money provider is also included to prove the payment layer can support multiple rails without changing the MCP tool API.

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
- operator dashboard API endpoints
- lightweight bearer-token operator auth and RBAC
- read-only reconciliation checks

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
        +--> Payment Provider Protocol
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
        +--> /approvals/*
        +--> /operator/*
              |
              +--> Operator bearer auth + RBAC
```

The MCP layer stays thin. Business decisions live in services and policies. External systems are behind protocols or repositories so mocks can be replaced later.

The FastAPI operator layer is also thin. It exposes visibility and human approval endpoints while delegating authorization to `app/auth/security.py` and business behavior to existing services.

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

## Multi-Rail Provider Demo

Payment execution is routed through a generic provider abstraction.

Default:

```env
PAYMENT_PROVIDER=daraja
```

Mock Airtel Money demo mode:

```env
PAYMENT_PROVIDER=airtel_mock
```

The Airtel provider is mock-only. It returns fake Airtel transaction IDs and stores transactions with:

```json
{
  "provider": "airtel",
  "rail": "airtel_money"
}
```

The legacy MCP tool `initiate_stk_push` remains available for compatibility with the current M-Pesa-first demo. The MCP server also exposes generic multi-rail tools:

- `initiate_payment`
- `check_payment_status`

The generic tools use the same `PaymentService`, `TransactionService`, provider abstraction, idempotency, rate limiting, approval workflow, audit logging, and governance controls.

## Local MCP Smoke Script

For the fastest end-to-end demo, run the local smoke script:

```bash
uv run python scripts/smoke_mcp_tools.py
```

The script uses `AppContainer` with in-memory storage and mock providers. It calls the MCP tool wrapper functions directly and prints clean JSON for each step.

The smoke output is grouped into:

- `legacy_mpesa_flow`
- `generic_daraja_flow`
- `generic_airtel_mock_flow`

The generic flows show provider metadata:

- `provider`
- `rail`
- `provider_transaction_id`
- `provider_reference`

The legacy flow still demonstrates:

- safe `initiate_stk_push`
- `check_transaction_status`
- simulated STK callback
- `generate_receipt`
- `get_today_summary`
- above-limit `initiate_stk_push` returning `approval_required`
- `approve_payment_request` executing the approved payment once
- `run_reconciliation`

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

## Safety & Governance Features Demonstrated

The demo is designed to show that payment-capable MCP tools can be wrapped in operational controls before they reach payment infrastructure.

### Tool Governance

Operators can control MCP tool access without code changes:

- `ENABLED_MCP_TOOLS`
- `BLOCKED_MCP_TOOLS`
- `APPROVAL_REQUIRED_MCP_TOOLS`

For example, `BLOCKED_MCP_TOOLS=initiate_stk_push` prevents the STK Push tool from executing and returns:

```json
{
  "status": "blocked",
  "allowed": false,
  "reason": "Tool disabled by policy"
}
```

`APPROVAL_REQUIRED_MCP_TOOLS=initiate_stk_push` returns:

```json
{
  "status": "approval_required",
  "allowed": false,
  "reason": "Tool requires approval by policy",
  "requires_approval": true
}
```

### Operator Security

The FastAPI operator and approval endpoints are protected separately from MCP tools.

Authentication is intentionally lightweight for the MVP:

- clients send `Authorization: Bearer <operator-token>`
- tokens map to simple roles
- raw tokens are never logged
- tests use fake tokens only

Configured roles:

- `viewer`: can read operator dashboard endpoints
- `approver`: can read operator endpoints and approve or reject pending payment requests
- `admin`: can access everything, including reconciliation runs

Environment variables:

```env
OPERATOR_AUTH_ENABLED=true
OPERATOR_VIEWER_TOKEN=
OPERATOR_APPROVER_TOKEN=
OPERATOR_ADMIN_TOKEN=
```

Protected routes:

```text
GET  /operator/transactions              viewer+
GET  /operator/transactions/{id}         viewer+
GET  /operator/audit-events              viewer+
GET  /operator/analytics/today           viewer+
POST /operator/reconciliation/run        admin
GET  /operator/ui                        browser console

GET  /approvals/pending                  approver+
GET  /approvals/{approval_id}            approver+
POST /approvals/{approval_id}/approve    approver+
POST /approvals/{approval_id}/reject     approver+
```

Example operator request:

```bash
curl http://localhost:8000/operator/transactions \
  -H "Authorization: Bearer $OPERATOR_VIEWER_TOKEN"
```

Missing or invalid tokens return:

```json
{
  "detail": "Invalid operator credentials."
}
```

Insufficient role access returns:

```json
{
  "detail": "Operator is not authorized for this action."
}
```

For local-only development, `OPERATOR_AUTH_ENABLED=false` allows access with a synthetic admin principal.

### Minimal Operator Console UI

The app also serves a tiny browser-based demo console:

```text
GET /operator/ui
```

Open it locally:

```text
http://localhost:8000/operator/ui
```

The console is intentionally simple:

- plain HTML, CSS, and vanilla JavaScript
- no React
- no build system
- no hardcoded tokens
- token is pasted by the user and stored only in browser `localStorage`

The UI calls the existing APIs:

- `/operator/analytics/today`
- `/operator/transactions`
- `/operator/audit-events`
- `/approvals/pending`
- `/approvals/{approval_id}/approve`
- `/approvals/{approval_id}/reject`
- `/operator/reconciliation/run`

It is useful for demos and reviewer walkthroughs, but it is not a production frontend.

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

### Callback Replay Protection

Duplicate callback payloads are rejected before transaction state is updated again. Replay detection uses a SHA256 fingerprint from stable callback fields:

- `CheckoutRequestID`
- `ResultCode`
- `MpesaReceiptNumber`

Duplicate callbacks return:

```json
{
  "status": "duplicate_callback",
  "success": false,
  "reason": "Duplicate callback replay detected."
}
```

### Audit Logging

Audit events are separate from operational logs and are intended for durable business/security tracking.

Tracked events include payment initiation, callback processing, duplicate callbacks, rejected callbacks, approvals, and receipt generation.

### Operator Visibility

The operator API exposes backend visibility without requiring a dashboard UI yet:

- recent transactions
- transaction details
- recent audit events
- today's analytics summary
- reconciliation run output

These endpoints are protected by the operator RBAC layer described above.

### Correlation IDs

FastAPI requests accept `X-Correlation-ID` and return the same header in responses. If a request does not include one, the app generates a UUID-based correlation ID.

The correlation ID is propagated into structured logs and audit events so reviewers can follow one request across the system.

### Structured Logs

Operational logs are JSON by default and include:

- UTC timestamp
- level
- logger name
- message
- event type
- correlation ID
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
- provide a production dashboard UI
- generate PDF receipts

It is a backend architecture MVP for safe AI-agent payment workflows.
