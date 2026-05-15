"""Add provider fields to transactions."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_provider_fields"
down_revision = "0003_audit_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("provider", sa.String(length=64), nullable=False, server_default="daraja"),
    )
    op.add_column(
        "transactions",
        sa.Column("rail", sa.String(length=64), nullable=False, server_default="mpesa"),
    )
    op.add_column(
        "transactions",
        sa.Column("provider_transaction_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "transactions",
        sa.Column("provider_reference", sa.String(length=128), nullable=True),
    )
    op.create_index("ix_transactions_provider", "transactions", ["provider"])
    op.create_index("ix_transactions_rail", "transactions", ["rail"])
    op.create_index(
        "ix_transactions_provider_transaction_id",
        "transactions",
        ["provider_transaction_id"],
    )
    op.create_index(
        "ix_transactions_provider_reference",
        "transactions",
        ["provider_reference"],
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_provider_reference", table_name="transactions")
    op.drop_index("ix_transactions_provider_transaction_id", table_name="transactions")
    op.drop_index("ix_transactions_rail", table_name="transactions")
    op.drop_index("ix_transactions_provider", table_name="transactions")
    op.drop_column("transactions", "provider_reference")
    op.drop_column("transactions", "provider_transaction_id")
    op.drop_column("transactions", "rail")
    op.drop_column("transactions", "provider")
