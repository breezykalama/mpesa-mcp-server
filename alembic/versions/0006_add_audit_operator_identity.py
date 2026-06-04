"""Add optional operator identity fields to audit events."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_audit_operator_identity"
down_revision = "0005_transaction_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("audit_events") as batch_op:
        batch_op.add_column(sa.Column("operator_subject", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("operator_email", sa.String(length=320), nullable=True))
        batch_op.add_column(
            sa.Column("operator_display_name", sa.String(length=255), nullable=True)
        )
        batch_op.create_index(
            "ix_audit_events_operator_subject",
            ["operator_subject"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("audit_events") as batch_op:
        batch_op.drop_index("ix_audit_events_operator_subject")
        batch_op.drop_column("operator_display_name")
        batch_op.drop_column("operator_email")
        batch_op.drop_column("operator_subject")
