# Production Readiness Checklist

This project is a production-shaped prototype, not a live production payment platform yet.

The backend and frontend already include tested vertical slices for MCP tools, Daraja sandbox requests, PostgreSQL persistence, Redis-backed controls, callback handling, approvals, reconciliation, audit logging, observability, Docker runtime, and an operator dashboard. The remaining work is mainly about real-money safety, identity, operational resilience, compliance, and production deployment discipline.

Status labels:

- **Done:** implemented in the current codebase.
- **Todo:** required before the relevant production stage.

## P0: Must-Have Before Any Real-Money Test

These items should be complete before sending even a small real payment through the system.

### Scope And Controls

- [x] **Done:** Define payment-capable actions behind explicit MCP tools.
- [x] **Done:** Keep MCP and FastAPI layers thin.
- [x] **Done:** Isolate business logic in services, policies, handlers, and providers.
- [x] **Done:** Preserve mock mode for local development and tests.
- [x] **Done:** Add provider abstraction for payment execution.
- [x] **Done:** Keep Daraja production mode disabled unless explicitly configured.
- [ ] **Todo:** Define the first real-money test scenario.
- [ ] **Todo:** Define the first real-money operator group.
- [ ] **Todo:** Define the first real-money maximum transaction amount.
- [ ] **Todo:** Define allowed production MCP tools.
- [ ] **Todo:** Confirm whether MCP access is internal-only for the first test.

### Daraja Real-Money Readiness

- [x] **Done:** Implement Daraja sandbox OAuth token retrieval.
- [x] **Done:** Implement Daraja sandbox STK Push submission.
- [x] **Done:** Implement Daraja sandbox Transaction Status submission.
- [x] **Done:** Keep Daraja sandbox behavior covered by mocked HTTP tests.
- [ ] **Todo:** Obtain production Safaricom Daraja credentials.
- [x] **Done:** Add carefully gated `DARAJA_MODE=production`.
- [x] **Done:** Separate sandbox and production credentials with backwards-compatible fallback variables.
- [ ] **Todo:** Validate production OAuth token flow with controlled credentials.
- [ ] **Todo:** Validate production STK Push with a low-value controlled test.
- [ ] **Todo:** Validate production transaction status with the correct Daraja transaction reference.
- [ ] **Todo:** Confirm production shortcode/till/paybill configuration.
- [ ] **Todo:** Confirm production callback URLs.
- [ ] **Todo:** Confirm production security credential generation process.
- [x] **Done:** Document production-mode configuration and hardening behavior.
- [ ] **Todo:** Add full production Daraja onboarding/setup runbook.

### Payment Safety

- [x] **Done:** Validate STK Push amount is present.
- [x] **Done:** Validate phone number is present.
- [x] **Done:** Block non-positive amounts.
- [x] **Done:** Require approval for above-limit payments.
- [x] **Done:** Block unknown payment actions.
- [x] **Done:** Add idempotency for payment initiation.
- [x] **Done:** Prevent duplicate provider calls for repeated idempotency keys.
- [x] **Done:** Add transaction state machine for local status changes.
- [x] **Done:** Protect terminal transaction states from illegal transitions.
- [x] **Done:** Prevent duplicate or late callbacks from overwriting completed or failed transactions.
- [x] **Done:** Add high-risk approval thresholds.
- [x] **Done:** Add multi-reviewer approval support.
- [ ] **Todo:** Add stricter phone number format validation.
- [ ] **Todo:** Add configurable limits per environment.
- [ ] **Todo:** Add merchant-specific or tenant-specific limits if needed.
- [ ] **Todo:** Add recipient allowlist/blocklist if needed.
- [ ] **Todo:** Add dynamic risk scoring.
- [ ] **Todo:** Add manual override policy and runbook.

### Approval Workflow

- [x] **Done:** Create approval requests for risky payments.
- [x] **Done:** Avoid calling provider while approval is pending.
- [x] **Done:** Execute approved payment exactly once.
- [x] **Done:** Preserve idempotency during approval execution.
- [x] **Done:** Support approval expiry.
- [x] **Done:** Support stale approval expiration.
- [x] **Done:** Prevent expired approvals from executing.
- [x] **Done:** Prevent rejected approvals from executing.
- [x] **Done:** Prevent the same reviewer approving twice.
- [x] **Done:** Show review progress in the React dashboard.
- [ ] **Todo:** Add approval comments or decision reason fields.
- [ ] **Todo:** Add operator notification mechanism for pending approvals.
- [ ] **Todo:** Add scheduled stale approval expiry outside manual endpoint calls.
- [ ] **Todo:** Add high-risk approval runbook.

### Callback Security

- [x] **Done:** Add optional callback shared secret validation.
- [x] **Done:** Reject missing or invalid callback secret when configured.
- [x] **Done:** Add callback replay protection.
- [x] **Done:** Support Redis-backed replay protection.
- [x] **Done:** Audit rejected callbacks.
- [x] **Done:** Audit duplicate callbacks.
- [x] **Done:** Add strict callback payload validation.
- [x] **Done:** Reject callbacks for unknown transactions.
- [x] **Done:** Validate callback amount against the local transaction when supplied.
- [x] **Done:** Validate callback phone number against the local transaction when supplied.
- [x] **Done:** Add source verification abstraction with development, trusted proxy, and strict block modes.
- [x] **Done:** Add trusted proxy header verification for production deployment.
- [x] **Done:** Disallow development callback source verification when Daraja production mode is enabled.
- [ ] **Todo:** Require callback secret outside local development.
- [x] **Done:** Add production callback source verification strategy using a trusted proxy boundary.
- [ ] **Todo:** Add stronger payload integrity checks if provider support is available.
- [ ] **Todo:** Document callback verification process.

### Persistence And Data Safety

- [x] **Done:** Add PostgreSQL transaction persistence.
- [x] **Done:** Add SQLAlchemy models.
- [x] **Done:** Add Alembic migrations.
- [x] **Done:** Add PostgreSQL audit persistence.
- [x] **Done:** Add unique idempotency lookup support.
- [x] **Done:** Add provider-aware transaction fields.
- [x] **Done:** Add database status check constraint for transaction states.
- [x] **Done:** Add database uniqueness enforcement for idempotency keys.
- [x] **Done:** Add database uniqueness enforcement for provider transaction IDs.
- [x] **Done:** Add production lookup indexes for transaction and audit paths.
- [ ] **Todo:** Add database-backed approval request persistence before adding approval request indexes.
- [ ] **Todo:** Add production database backup strategy.
- [ ] **Todo:** Test database restore process.
- [ ] **Todo:** Define audit retention policy.
- [ ] **Todo:** Define PII retention policy.

### Secrets And Configuration

- [x] **Done:** Keep `.env` ignored.
- [x] **Done:** Keep `.env.example` placeholder-based.
- [x] **Done:** Avoid real credentials in README and demo docs.
- [x] **Done:** Avoid logging known secret fields.
- [x] **Done:** Add startup validation for Daraja production credentials.
- [x] **Done:** Add startup validation for operator auth configuration.
- [x] **Done:** Add startup validation for callback shared secret outside development.
- [x] **Done:** Add startup validation for Redis and Postgres mode configuration.
- [x] **Done:** Add startup dependency validation for configured Postgres and Redis modes.
- [x] **Done:** Add graceful infrastructure failure policy for Postgres and Redis outages.
- [x] **Done:** Fail closed when Redis-backed rate limiting or callback replay protection is unavailable.
- [x] **Done:** Strengthen readiness checks with dependency details.
- [x] **Done:** Add CI secret scanning with gitleaks.
- [x] **Done:** Add security model documentation for secret handling expectations.
- [ ] **Todo:** Use a real secret manager for production credentials.
- [ ] **Todo:** Store Daraja credentials outside flat files.
- [ ] **Todo:** Store callback secret outside flat files.
- [ ] **Todo:** Store operator auth secrets outside flat files.
- [ ] **Todo:** Add secret rotation process.
- [ ] **Todo:** Add secret scanning in CI.

### Observability

- [x] **Done:** Add structured JSON application logs.
- [x] **Done:** Add audit logging separate from operational logs.
- [x] **Done:** Add correlation ID tracing.
- [x] **Done:** Add `/health`.
- [x] **Done:** Add `/health/ready`.
- [x] **Done:** Add `/metrics`.
- [x] **Done:** Track payment, approval, callback, and receipt metrics.
- [ ] **Todo:** Add production alerting for failed payments.
- [ ] **Todo:** Add production alerting for callback failures.
- [ ] **Todo:** Add production alerting for provider downtime.
- [ ] **Todo:** Add production alerting for approval backlog.

## P1: Required Before Controlled Internal Launch

These items should be complete before internal users or operators depend on the system.

### Authentication And Authorization

- [x] **Done:** Add lightweight operator bearer-token authentication.
- [x] **Done:** Add viewer, approver, and admin roles.
- [x] **Done:** Protect operator endpoints.
- [x] **Done:** Protect approval endpoints.
- [x] **Done:** Prevent viewers from approving payments.
- [x] **Done:** Restrict reconciliation run endpoint to admins.
- [x] **Done:** Avoid logging raw tokens.
- [ ] **Todo:** Replace static operator tokens with SSO/OAuth.
- [ ] **Todo:** Add real operator identity provider.
- [ ] **Todo:** Add token/session expiry.
- [ ] **Todo:** Add token rotation process.
- [ ] **Todo:** Add least-privilege access review process.

### MCP Governance

- [x] **Done:** Add configurable MCP tool policy.
- [x] **Done:** Support enabled, blocked, and approval-required tool lists.
- [x] **Done:** Rate limit sensitive MCP tools.
- [x] **Done:** Add Redis-backed rate limiter.
- [x] **Done:** Audit MCP-driven payment and approval activity.
- [ ] **Todo:** Define trusted MCP clients.
- [ ] **Todo:** Add MCP client authentication if MCP is exposed beyond local/internal use.
- [ ] **Todo:** Add per-client rate limits.
- [ ] **Todo:** Add per-client audit attribution.
- [ ] **Todo:** Document safe MCP access boundaries.

### Provider Resilience

- [x] **Done:** Handle Daraja HTTP/network failures with clean failed responses for implemented flows.
- [x] **Done:** Handle invalid Daraja JSON responses for transaction status.
- [ ] **Todo:** Add provider timeout settings per call.
- [x] **Done:** Add retry policy with backoff for OAuth and transaction status transient failures.
- [x] **Done:** Avoid retrying STK Push in the Daraja client without explicit idempotency safety context.
- [x] **Done:** Add circuit breaker for Daraja outages.
- [x] **Done:** Add provider error classification.
- [ ] **Todo:** Add provider latency metrics.

### Reconciliation

- [x] **Done:** Add reconciliation service.
- [x] **Done:** Detect local/provider status mismatches.
- [x] **Done:** Detect stale pending transactions.
- [x] **Done:** Add MCP reconciliation tool.
- [x] **Done:** Add operator reconciliation endpoint.
- [x] **Done:** Add reconciliation panel in the dashboard.
- [ ] **Todo:** Add scheduled reconciliation job.
- [ ] **Todo:** Add reconciliation alerting.
- [ ] **Todo:** Add reconciliation report export.
- [ ] **Todo:** Add manual remediation workflow.

### Operator Experience

- [x] **Done:** Add operator APIs for transactions, audit events, analytics, reconciliation, receipts, and approvals.
- [x] **Done:** Add React operator dashboard.
- [x] **Done:** Add login/token screen.
- [x] **Done:** Add dashboard overview.
- [x] **Done:** Add transaction filtering, search, and sorting.
- [x] **Done:** Add pending approvals panel.
- [x] **Done:** Add audit and callback timelines.
- [x] **Done:** Add receipt lookup and JSON export.
- [x] **Done:** Add system status panel.
- [ ] **Todo:** Add server-side pagination for large transaction lists.
- [ ] **Todo:** Add server-side filtering and search for large datasets.
- [ ] **Todo:** Add transaction detail page.
- [ ] **Todo:** Add approval decision comments in UI.
- [ ] **Todo:** Add frontend error reporting.
- [ ] **Todo:** Complete accessibility review.

### Docker And Deployment

- [x] **Done:** Add Dockerfile.
- [x] **Done:** Add Docker Compose with app, PostgreSQL, and Redis.
- [x] **Done:** Add PostgreSQL healthcheck.
- [x] **Done:** Add Redis healthcheck.
- [x] **Done:** Add automatic Alembic migrations on Docker startup.
- [x] **Done:** Add local Docker health validation instructions.
- [ ] **Todo:** Add non-root container user.
- [ ] **Todo:** Review image size and dependency layers.
- [ ] **Todo:** Decide whether production migrations run as a separate job.
- [ ] **Todo:** Add staging deployment environment.
- [ ] **Todo:** Add rollback process.

### CI And Quality Gates

- [x] **Done:** Add GitHub Actions backend CI.
- [x] **Done:** Run compile checks in CI.
- [x] **Done:** Run pytest in CI.
- [x] **Done:** Run Ruff in CI.
- [x] **Done:** Run mypy in CI.
- [x] **Done:** Add frontend CI.
- [x] **Done:** Run frontend tests in CI.
- [x] **Done:** Run frontend production build in CI.
- [ ] **Todo:** Add dependency vulnerability scanning.
- [ ] **Todo:** Add Docker image build in CI.
- [ ] **Todo:** Add Docker image vulnerability scanning.
- [ ] **Todo:** Add migration validation in CI.
- [ ] **Todo:** Add release tagging.

## P2: Required Before Broader Production Use

These items matter once more users, higher amounts, or external dependencies enter the picture.

### Production Identity And Access

- [ ] **Todo:** Implement SSO/OAuth-backed operator identity.
- [ ] **Todo:** Add reviewer groups or delegated approval policy.
- [ ] **Todo:** Add admin access review workflow.
- [ ] **Todo:** Add environment-specific role assignments.
- [ ] **Todo:** Add break-glass admin process.

### Payment And Risk Maturity

- [ ] **Todo:** Add dynamic risk scoring.
- [ ] **Todo:** Add velocity checks for repeated payment requests.
- [ ] **Todo:** Add suspicious activity detection.
- [ ] **Todo:** Add environment-specific and merchant-specific limits.
- [ ] **Todo:** Add approval escalation rules.
- [ ] **Todo:** Add refund, reversal, or cancellation strategy if in scope.
- [ ] **Todo:** Add formal compliance review for live payment use.

### Callback And Provider Verification

- [ ] **Todo:** Validate callback source using provider-supported methods.
- [ ] **Todo:** Add payload integrity verification where available.
- [ ] **Todo:** Add provider reference matching rules.
- [ ] **Todo:** Add stronger reconciliation between callback state and provider query state.
- [ ] **Todo:** Add alerting for suspicious callback patterns.

### Data Protection And Compliance

- [ ] **Todo:** Identify all stored personal data.
- [ ] **Todo:** Mask phone numbers in logs and UI where appropriate.
- [ ] **Todo:** Define data deletion process.
- [ ] **Todo:** Define audit event retention period.
- [ ] **Todo:** Review Safaricom/Daraja terms.
- [ ] **Todo:** Review local data protection requirements.
- [ ] **Todo:** Add privacy notice if user-facing.
- [ ] **Todo:** Add compliance documentation for operators.

### Receipts And Reporting

- [x] **Done:** Generate structured receipts for completed transactions.
- [x] **Done:** Add receipt lookup endpoint.
- [x] **Done:** Add JSON export in dashboard.
- [ ] **Todo:** Add PDF receipt export.
- [ ] **Todo:** Add receipt template.
- [ ] **Todo:** Add legal/tax receipt review.
- [ ] **Todo:** Add durable receipt storage strategy if required.
- [ ] **Todo:** Add report export for finance operations.

### Reliability And Scale

- [ ] **Todo:** Add load testing for payment initiation.
- [ ] **Todo:** Add load testing for callback ingestion.
- [ ] **Todo:** Add database connection pooling review.
- [ ] **Todo:** Add Redis outage behavior tests.
- [ ] **Todo:** Add database outage behavior tests.
- [ ] **Todo:** Add dead-letter or retry queue for failed callback processing if needed.
- [ ] **Todo:** Add high-availability deployment plan.

### Operations And Runbooks

- [ ] **Todo:** Add Daraja outage runbook.
- [ ] **Todo:** Add callback outage runbook.
- [ ] **Todo:** Add Redis outage runbook.
- [ ] **Todo:** Add PostgreSQL outage runbook.
- [ ] **Todo:** Add reconciliation mismatch runbook.
- [ ] **Todo:** Add approval backlog runbook.
- [ ] **Todo:** Add suspicious activity runbook.
- [ ] **Todo:** Add credential rotation runbook.

## P3: Post-Launch Maturity

These items improve long-term maintainability, operations, and product depth after a controlled launch.

### Provider Expansion

- [x] **Done:** Prove multi-rail architecture with Airtel Money mock provider.
- [ ] **Todo:** Add more real payment rails beyond Daraja.
- [ ] **Todo:** Add real Airtel Money provider if required.
- [ ] **Todo:** Add MTN MoMo provider if required.
- [ ] **Todo:** Add bank transfer provider if required.
- [ ] **Todo:** Add provider capability matrix.

### Advanced Operations

- [ ] **Todo:** Add production dashboard for logs, metrics, and alerts.
- [ ] **Todo:** Add SLOs and error budgets.
- [ ] **Todo:** Add monthly access review.
- [ ] **Todo:** Add automated backup restore drills.
- [ ] **Todo:** Add incident review process.
- [ ] **Todo:** Add operational analytics for provider reliability.

### Product And Dashboard Maturity

- [ ] **Todo:** Add role-aware dashboard navigation.
- [ ] **Todo:** Add richer transaction detail views.
- [ ] **Todo:** Add reconciliation remediation UI.
- [ ] **Todo:** Add approval notification inbox.
- [ ] **Todo:** Add finance export workflows.
- [ ] **Todo:** Add dashboard deployment pipeline.

### Documentation Maturity

- [x] **Done:** Add README.
- [x] **Done:** Add demo guide.
- [x] **Done:** Add production readiness checklist.
- [ ] **Todo:** Add production setup guide.
- [ ] **Todo:** Add Daraja production setup guide.
- [ ] **Todo:** Add environment variable reference.
- [ ] **Todo:** Add operator handbook.
- [ ] **Todo:** Add architecture decision records.
- [ ] **Todo:** Add API and MCP tool reference.

## Pre-Launch Gate

Before any controlled real-money launch, verify:

- [ ] **Todo:** All P0 items are complete.
- [ ] **Todo:** CI is green.
- [ ] **Todo:** No production secrets are committed.
- [ ] **Todo:** Production environment variables are configured securely.
- [ ] **Todo:** Database migrations are applied in staging.
- [ ] **Todo:** Staging smoke test passes.
- [ ] **Todo:** Callback endpoint is reachable.
- [ ] **Todo:** Callback security is enabled.
- [ ] **Todo:** Redis is enabled for rate limiting and callback replay protection.
- [ ] **Todo:** Operator auth is enabled.
- [ ] **Todo:** Admin, approver, and viewer roles are tested.
- [ ] **Todo:** Monitoring and alerting are active.
- [ ] **Todo:** Backup and restore process is tested.
- [ ] **Todo:** Rollback process is documented.
- [ ] **Todo:** First low-value production transaction test plan is approved.
