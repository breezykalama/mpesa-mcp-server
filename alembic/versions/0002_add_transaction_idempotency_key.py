"""Add transaction idempotency key."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_add_transaction_idempotency_key"
down_revision = "0001_create_transactions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_transactions_idempotency_key",
        "transactions",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_idempotency_key", table_name="transactions")
    op.drop_column("transactions", "idempotency_key")
