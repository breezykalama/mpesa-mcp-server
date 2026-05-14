"""Approval workflow models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ApprovalStatus = Literal["pending", "approved", "rejected", "expired"]


class ApprovalRequest(BaseModel):
    """Approval request for a risky action."""

    approval_id: str
    action: str
    payload: dict[str, Any]
    reason: str
    status: ApprovalStatus = "pending"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reviewed_at: datetime | None = None
