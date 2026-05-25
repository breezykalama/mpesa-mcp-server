# Production Validation Runbook

## Objective

Validate the platform safely using controlled low-value real-money transactions.

This runbook is for internal launch validation only. It does not mean the platform is broadly production-ready, and it must not be used to justify large-value or public payment workflows without a separate launch approval.

The goal is to prove that the production configuration, provider integration, callback path, audit trail, operator workflow, and rollback controls behave correctly before any wider use.

## Preconditions Checklist

Complete every item before starting Phase 1.

- [ ] Production credentials have been obtained from Safaricom.
- [ ] Production shortcode, passkey, security credential, and callback URL have been confirmed.
- [ ] Production callback URL is publicly reachable through the approved ingress path.
- [ ] `DARAJA_MODE=production` is configured only in the intended validation environment.
- [ ] `CALLBACK_SOURCE_VERIFICATION_MODE=trusted_proxy` is enabled.
- [ ] Trusted proxy callback verification is configured.
- [ ] Callback shared secret validation is configured if required by the deployment.
- [ ] Production secrets are stored securely outside the repository.
- [ ] Operator auth is enabled.
- [ ] Admin, approver, and viewer roles have been tested.
- [ ] Redis is enabled for rate limiting and callback replay protection.
- [ ] PostgreSQL is enabled for transaction and audit persistence.
- [ ] `/health` returns healthy.
- [ ] `/health/ready` returns ready.
- [ ] CI is green.
- [ ] Secret scan is green.
- [ ] Rollback plan is documented.
- [ ] Backup strategy is ready.
- [ ] Monitoring, structured logs, and audit visibility are available.
- [ ] Approval workflow has been tested in the target environment.
- [ ] Production configuration validation passes at startup.

## Test Transaction Plan

Use staged real-money validation. Do not begin with large amounts.

Stop after any unexplained failure. Do not continue to a higher-value phase until the current phase is cleanly understood and documented.

### Phase 1: KES 1-10

Use one controlled test phone number and one approved operator.

Validate:

- OAuth token acquisition succeeds.
- Payment initiation request is accepted by Daraja.
- Provider response is normalized correctly.
- Transaction is persisted with provider metadata.
- Callback is received.
- Callback secret and trusted proxy validation pass.
- Callback replay protection records the callback.
- Callback payload validates against the known transaction.
- Amount and phone metadata match when supplied.
- Transaction state moves from `pending` to the provider-reported terminal state.
- Audit events are written with correlation IDs.
- Receipt generation works for a completed transaction.
- Operator dashboard shows the transaction, callback/audit events, and receipt.
- Metrics and structured logs are visible.
- Reconciliation output is sane.

### Phase 2: KES 20-100

Repeat the Phase 1 checks with a slightly higher amount.

Additionally validate:

- Rate limits remain active.
- Approval rules behave as expected if the amount crosses configured limits.
- No duplicate provider execution occurs after retrying the same idempotency key.
- Operator approval actions are audit logged.
- Reconciliation does not report unexplained mismatches.

### Phase 3: KES 100-500

Repeat the Phase 1 and Phase 2 checks with the highest approved validation amount.

Additionally validate:

- Monitoring visibility is sufficient for an operator to explain the payment lifecycle.
- Callback latency is acceptable for the intended launch scope.
- Provider latency is acceptable for the intended launch scope.
- No unexpected errors appear in application logs.
- No unexpected audit gaps exist.

## Failure Handling Checklist

For any failure:

- Stop progression immediately.
- Do not move to a higher-value phase.
- Capture correlation ID, transaction ID, provider reference, timestamp, and operator ID where available.
- Document the incident.
- Investigate root cause.
- Decide whether to retry the same phase, rollback, or stop the validation session.

### OAuth Fails

- Confirm production consumer key and secret are present in the secret store.
- Confirm the app is using the production Daraja base URL.
- Check for `auth_error` classification.
- Do not retry higher-value tests until authentication succeeds consistently.

### STK Push Fails

- Confirm shortcode, passkey, callback URL, and phone format.
- Review provider response and normalized error category.
- Confirm the transaction was not persisted as successful if provider initiation failed.
- Retry only with a clear idempotency strategy.

### Callback Never Arrives

- Confirm production callback URL registration.
- Confirm reverse proxy, ingress, TLS, and routing.
- Check application logs for rejected callbacks.
- Run reconciliation to detect provider/local mismatch.

### Callback Rejected

- Check rejection reason: shared secret, trusted proxy, replay, schema, unknown transaction, amount mismatch, phone mismatch, or invalid transition.
- Confirm the callback came through the approved ingress path.
- Do not bypass callback validation to force completion.

### Amount Mismatch

- Treat as a validation failure.
- Do not mutate the transaction.
- Capture audit event details.
- Confirm original payment request amount and provider callback amount.

### Phone Mismatch

- Treat as a validation failure.
- Do not mutate the transaction.
- Confirm phone normalization expectations.
- Confirm the callback belongs to the intended payment request.

### Replay Rejection

- Confirm whether it is a true duplicate callback.
- Confirm transaction state did not change unexpectedly.
- Verify audit event exists for the duplicate callback.

### Database Unavailable

- Stop validation.
- Confirm readiness reports PostgreSQL unavailable.
- Do not process payment-capable workflows until persistence is healthy.
- Restore service or rollback.

### Redis Unavailable

- Stop validation.
- Confirm readiness reports Redis unavailable when Redis-backed protections are configured.
- Do not process payment-capable workflows because rate limiting and callback replay protection must fail closed.
- Restore service or rollback.

### Provider Timeout

- Confirm timeout classification.
- Check circuit breaker state.
- Do not keep retrying STK Push without an approved idempotency-safe process.
- Use reconciliation before deciding whether a payment was accepted by the provider.

### Circuit Breaker Opens

- Stop payment initiation.
- Confirm provider outage or repeated provider errors.
- Wait for recovery window and retry only the same low-value phase after review.

### Operator Auth Issue

- Confirm the correct role token is being used.
- Confirm token values are not logged.
- Do not disable auth to proceed with validation.

## Rollback Procedure

Use the smallest rollback needed to stop risk safely.

Options:

- Switch `DARAJA_MODE` away from `production`.
- Disable payment-capable MCP tools through tool policy.
- Disable operator approval execution by removing approver/admin access.
- Block callback ingress at the proxy, gateway, or ingress layer.
- Switch `CALLBACK_SOURCE_VERIFICATION_MODE=strict_block`.
- Rotate the trusted proxy shared secret.
- Rotate the callback shared secret.
- Revoke or rotate Daraja production credentials if compromise is suspected.
- Stop the application if persistence, replay protection, or rate limiting cannot be trusted.

After rollback:

- Preserve logs and audit events.
- Record the exact configuration change.
- Document whether any payment was initiated or completed.
- Run reconciliation before resuming validation.

## Launch Observation Checklist

During each validation phase, observe:

- Provider latency.
- Callback latency.
- Failed payment count.
- Approval backlog.
- Rate-limit events.
- Callback replay events.
- Callback validation failures.
- Audit event completeness.
- Unexpected application errors.
- Circuit breaker state.
- Reconciliation anomalies.
- Operator dashboard visibility.

## Go/No-Go Criteria

Production validation is a **GO** only if:

- All low-value phases succeed.
- Callback trust validation succeeds.
- Transaction integrity is preserved.
- No duplicate provider execution occurs.
- Audit trail is complete.
- Operator workflows function.
- Receipt generation works for completed transactions.
- Metrics and logs are sufficient for troubleshooting.
- Reconciliation output is sane.
- No unexplained failures remain open.

Otherwise the result is **NO GO**.

If any required check is skipped, the result is **NO GO**.

## Post-Validation Review

After the validation session:

- Review incidents and unexpected behavior.
- Review audit events and correlation IDs.
- Review transaction records and provider references.
- Review reconciliation results.
- Record hardening tasks.
- Decide whether credentials should be rotated.
- Decide whether to proceed, repeat controlled validation, or remain staged.

Do not expand usage until the post-validation decision is documented.
