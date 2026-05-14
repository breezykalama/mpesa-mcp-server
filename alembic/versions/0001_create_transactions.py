"""Create transactions table."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_create_transactions"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transactions",
        sa.Column("transaction_id", sa.String(length=36), nullable=False),
        sa.Column("checkout_request_id", sa.String(length=128), nullable=False),
        sa.Column("merchant_request_id", sa.String(length=128), nullable=False),
        sa.Column("phone_number", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("account_reference", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("result_code", sa.Integer(), nullable=True),
        sa.Column("result_description", sa.String(length=255), nullable=True),
        sa.Column("mpesa_receipt_number", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("transaction_id"),
        sa.UniqueConstraint("checkout_request_id"),
    )
    op.create_index(
        "ix_transactions_checkout_request_id",
        "transactions",
        ["checkout_request_id"],
    )
    op.create_index("ix_transactions_created_at", "transactions", ["created_at"])
    op.create_index("ix_transactions_merchant_request_id", "transactions", ["merchant_request_id"])
    op.create_index("ix_transactions_status", "transactions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_transactions_status", table_name="transactions")
    op.drop_index("ix_transactions_merchant_request_id", table_name="transactions")
    op.drop_index("ix_transactions_created_at", table_name="transactions")
    op.drop_index("ix_transactions_checkout_request_id", table_name="transactions")
    op.drop_table("transactions")
