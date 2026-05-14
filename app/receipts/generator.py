"""Receipt generator."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel

from app.storage.repositories import PendingTransaction


class Receipt(BaseModel):
    """Generated receipt for a completed transaction."""

    receipt_id: str
    transaction_id: str
    checkout_request_id: str
    mpesa_receipt_number: str
    amount: int
    phone_number: str
    status: str
    issued_at: datetime


class ReceiptGenerationResult(BaseModel):
    """Result returned by receipt generation."""

    status: str
    allowed: bool
    reason: str
    receipt: Receipt | None = None


class ReceiptGenerator:
    """Generate receipts from completed transactions."""

    def generate(self, transaction: PendingTransaction) -> ReceiptGenerationResult:
        """Generate a receipt for a completed transaction."""

        if transaction.status != "completed":
            return ReceiptGenerationResult(
                status="blocked",
                allowed=False,
                reason="Only completed transactions can generate receipts.",
            )

        if transaction.mpesa_receipt_number is None:
            return ReceiptGenerationResult(
                status="blocked",
                allowed=False,
                reason="Completed transaction is missing an M-Pesa receipt number.",
            )

        return ReceiptGenerationResult(
            status="generated",
            allowed=True,
            reason="Receipt generated successfully.",
            receipt=Receipt(
                receipt_id=str(uuid4()),
                transaction_id=transaction.transaction_id,
                checkout_request_id=transaction.checkout_request_id,
                mpesa_receipt_number=transaction.mpesa_receipt_number,
                amount=transaction.amount,
                phone_number=transaction.phone_number,
                status=transaction.status,
                issued_at=datetime.now(UTC),
            ),
        )
