"""Add transaction integrity constraints and lookup indexes."""

from __future__ import annotations

from alembic import op

revision = "0005_transaction_integrity"
down_revision = "0004_provider_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.create_check_constraint(
            "ck_transactions_status_allowed",
            "status IN ('pending', 'completed', 'failed', 'timed_out', 'cancelled')",
        )
        batch_op.create_unique_constraint(
            "uq_transactions_idempotency_key",
            ["idempotency_key"],
        )
        batch_op.create_unique_constraint(
            "uq_transactions_provider_transaction_id",
            ["provider_transaction_id"],
        )

    op.create_index(
        "ix_transactions_phone_number",
        "transactions",
        ["phone_number"],
    )
    op.create_index(
        "ix_transactions_provider_status",
        "transactions",
        ["provider", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_provider_status", table_name="transactions")
    op.drop_index("ix_transactions_phone_number", table_name="transactions")

    with op.batch_alter_table("transactions") as batch_op:
        batch_op.drop_constraint(
            "uq_transactions_provider_transaction_id",
            type_="unique",
        )
        batch_op.drop_constraint(
            "uq_transactions_idempotency_key",
            type_="unique",
        )
        batch_op.drop_constraint(
            "ck_transactions_status_allowed",
            type_="check",
        )
