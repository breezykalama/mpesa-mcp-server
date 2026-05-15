"""Read-only transaction reconciliation service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from app.payments.providers import PaymentProviderProtocol, PaymentStatusResponse
from app.storage.repositories import PendingTransaction, TransactionRepositoryProtocol

RECONCILABLE_STATUSES = frozenset({"pending", "completed", "failed"})
FINAL_PROVIDER_STATUSES = frozenset({"completed", "failed"})


class ReconciliationFinding(BaseModel):
    """A single reconciliation inconsistency."""

    finding_type: str
    transaction_id: str
    checkout_request_id: str
    local_status: str
    provider_status: str | None = None
    provider: str
    rail: str
    reason: str


class ReconciliationSummary(BaseModel):
    """Summary returned after reconciliation."""

    status: str = "ok"
    checked_transactions: int = 0
    finding_count: int = 0
    pending_local_but_provider_completed: int = 0
    pending_local_but_provider_failed: int = 0
    completed_local_but_provider_failed: int = 0
    failed_local_but_provider_completed: int = 0
    stale_pending_transaction: int = 0
    provider_status_unknown: int = 0
    findings: list[ReconciliationFinding] = Field(default_factory=list)


class ReconciliationService:
    """Detect local/provider transaction status inconsistencies."""

    def __init__(
        self,
        *,
        transaction_repository: TransactionRepositoryProtocol,
        payment_provider: PaymentProviderProtocol,
        stale_pending_minutes: int,
    ) -> None:
        self._transaction_repository = transaction_repository
        self._payment_provider = payment_provider
        self._stale_pending_minutes = stale_pending_minutes

    def run_reconciliation(self) -> ReconciliationSummary:
        """Run a read-only reconciliation pass."""

        findings: list[ReconciliationFinding] = []
        checked_transactions = 0
        now = datetime.now(UTC)

        for transaction in self._transaction_repository.list_transactions():
            if transaction.status not in RECONCILABLE_STATUSES:
                continue

            checked_transactions += 1
            findings.extend(self._stale_pending_findings(transaction, now))
            findings.extend(self._provider_mismatch_findings(transaction))

        return self._build_summary(
            checked_transactions=checked_transactions,
            findings=findings,
        )

    def _provider_mismatch_findings(
        self,
        transaction: PendingTransaction,
    ) -> list[ReconciliationFinding]:
        provider_status = self._provider_status(transaction)
        if provider_status is None or provider_status.status not in FINAL_PROVIDER_STATUSES:
            return [
                self._finding(
                    "provider_status_unknown",
                    transaction,
                    provider_status.status if provider_status is not None else None,
                    "Provider status could not be resolved to a final state.",
                )
            ]

        if transaction.status == "pending" and provider_status.status == "completed":
            return [
                self._finding(
                    "pending_local_but_provider_completed",
                    transaction,
                    provider_status.status,
                    "Local transaction is pending but provider reports completed.",
                )
            ]

        if transaction.status == "pending" and provider_status.status == "failed":
            return [
                self._finding(
                    "pending_local_but_provider_failed",
                    transaction,
                    provider_status.status,
                    "Local transaction is pending but provider reports failed.",
                )
            ]

        if transaction.status == "completed" and provider_status.status == "failed":
            return [
                self._finding(
                    "completed_local_but_provider_failed",
                    transaction,
                    provider_status.status,
                    "Local transaction is completed but provider reports failed.",
                )
            ]

        if transaction.status == "failed" and provider_status.status == "completed":
            return [
                self._finding(
                    "failed_local_but_provider_completed",
                    transaction,
                    provider_status.status,
                    "Local transaction is failed but provider reports completed.",
                )
            ]

        return []

    def _provider_status(
        self,
        transaction: PendingTransaction,
    ) -> PaymentStatusResponse | None:
        reference = transaction.provider_transaction_id or transaction.checkout_request_id
        try:
            return self._payment_provider.check_transaction_status(reference)
        except Exception:
            return None

    def _stale_pending_findings(
        self,
        transaction: PendingTransaction,
        now: datetime,
    ) -> list[ReconciliationFinding]:
        if transaction.status != "pending":
            return []

        threshold = now - timedelta(minutes=self._stale_pending_minutes)
        if transaction.created_at > threshold:
            return []

        return [
            self._finding(
                "stale_pending_transaction",
                transaction,
                None,
                "Local pending transaction is older than the configured threshold.",
            )
        ]

    def _finding(
        self,
        finding_type: str,
        transaction: PendingTransaction,
        provider_status: str | None,
        reason: str,
    ) -> ReconciliationFinding:
        return ReconciliationFinding(
            finding_type=finding_type,
            transaction_id=transaction.transaction_id,
            checkout_request_id=transaction.checkout_request_id,
            local_status=transaction.status,
            provider_status=provider_status,
            provider=transaction.provider,
            rail=transaction.rail,
            reason=reason,
        )

    def _build_summary(
        self,
        *,
        checked_transactions: int,
        findings: list[ReconciliationFinding],
    ) -> ReconciliationSummary:
        counts = {
            "pending_local_but_provider_completed": 0,
            "pending_local_but_provider_failed": 0,
            "completed_local_but_provider_failed": 0,
            "failed_local_but_provider_completed": 0,
            "stale_pending_transaction": 0,
            "provider_status_unknown": 0,
        }
        for finding in findings:
            counts[finding.finding_type] += 1

        return ReconciliationSummary(
            checked_transactions=checked_transactions,
            finding_count=len(findings),
            findings=findings,
            pending_local_but_provider_completed=counts[
                "pending_local_but_provider_completed"
            ],
            pending_local_but_provider_failed=counts["pending_local_but_provider_failed"],
            completed_local_but_provider_failed=counts[
                "completed_local_but_provider_failed"
            ],
            failed_local_but_provider_completed=counts[
                "failed_local_but_provider_completed"
            ],
            stale_pending_transaction=counts["stale_pending_transaction"],
            provider_status_unknown=counts["provider_status_unknown"],
        )
