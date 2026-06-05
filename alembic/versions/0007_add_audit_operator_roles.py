"""Add optional mapped operator roles to audit events."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_audit_operator_roles"
down_revision = "0006_audit_operator_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("audit_events") as batch_op:
        batch_op.add_column(sa.Column("operator_roles", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("audit_events") as batch_op:
        batch_op.drop_column("operator_roles")
