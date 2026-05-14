"""Create audit events table."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_create_audit_events"
down_revision = "0002_add_transaction_idempotency_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_audit_events_correlation_id", "audit_events", ["correlation_id"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_event_type", table_name="audit_events")
    op.drop_index("ix_audit_events_created_at", table_name="audit_events")
    op.drop_index("ix_audit_events_correlation_id", table_name="audit_events")
    op.drop_table("audit_events")
