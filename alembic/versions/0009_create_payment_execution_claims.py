"""Create durable payment execution claims (Slice E1).

An execution claim records that exactly one internal worker has been granted the right
to execute the payment of one approved durable approval request. Claims are separate
from approval status: creating a claim never changes the approval request, its quorum
or its decision history, and nothing here talks to a payment provider.

Declarative guarantees (all dialects):

* one claim per approval (``uq_payment_execution_claims_approval_id``);
* a composite foreign key binds the claim to the approval's stored payload hash;
* the only state is ``claimed``; provider outcome states belong to a later slice;
* only a SHA-256 hash of the unpredictable claim token is stored.

On PostgreSQL the migration also installs guard triggers:

* a claim can only be inserted for an approval whose persisted status is ``approved``
  and whose action is a supported payment execution action (``initiate_stk_push``);
  both are immutable once approved, so the invariant holds for the claim's lifetime;
* claim identity, approval binding, token hash, worker attribution and creation time
  are immutable;
* claims cannot be deleted or truncated.

SQLite (local development) gets the table and declarative constraints only.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_payment_execution_claims"
down_revision = "0008_durable_approvals"
branch_labels = None
depends_on = None

EXECUTABLE_PAYMENT_ACTIONS = ("initiate_stk_push",)
"""Mirrors ``app.payments.execution_claims.EXECUTABLE_PAYMENT_ACTIONS`` (tested)."""

_ACTION_LIST_SQL = ", ".join(f"'{action}'" for action in EXECUTABLE_PAYMENT_ACTIONS)

POSTGRES_GUARD_FUNCTIONS = (
    f"""
    CREATE FUNCTION payment_execution_claims_require_approved() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
        approval_status text;
        approval_action text;
    BEGIN
        SELECT status, action INTO approval_status, approval_action
        FROM approval_requests
        WHERE approval_id = NEW.approval_id;

        IF approval_status IS DISTINCT FROM 'approved' THEN
            RAISE EXCEPTION 'approval request % is not approved and cannot be claimed',
                NEW.approval_id
                USING ERRCODE = 'restrict_violation';
        END IF;

        IF approval_action IS NULL OR approval_action NOT IN ({_ACTION_LIST_SQL}) THEN
            RAISE EXCEPTION 'approval request % is not a payment execution action',
                NEW.approval_id
                USING ERRCODE = 'restrict_violation';
        END IF;

        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE FUNCTION payment_execution_claims_guard() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' OR TG_OP = 'TRUNCATE' THEN
            RAISE EXCEPTION 'payment_execution_claims rows cannot be deleted'
                USING ERRCODE = 'restrict_violation';
        END IF;

        IF NEW.claim_id IS DISTINCT FROM OLD.claim_id
            OR NEW.approval_id IS DISTINCT FROM OLD.approval_id
            OR NEW.payload_hash IS DISTINCT FROM OLD.payload_hash
            OR NEW.claim_token_hash IS DISTINCT FROM OLD.claim_token_hash
            OR NEW.claimed_by IS DISTINCT FROM OLD.claimed_by
            OR NEW.correlation_id IS DISTINCT FROM OLD.correlation_id
            OR NEW.claimed_at IS DISTINCT FROM OLD.claimed_at
        THEN
            RAISE EXCEPTION 'payment execution claim % immutable fields cannot change',
                OLD.claim_id
                USING ERRCODE = 'restrict_violation';
        END IF;

        RETURN NEW;
    END;
    $$
    """,
)

POSTGRES_GUARD_TRIGGERS = (
    """
    CREATE TRIGGER trg_payment_execution_claims_require_approved
    BEFORE INSERT ON payment_execution_claims
    FOR EACH ROW EXECUTE FUNCTION payment_execution_claims_require_approved()
    """,
    """
    CREATE TRIGGER trg_payment_execution_claims_guard
    BEFORE UPDATE OR DELETE ON payment_execution_claims
    FOR EACH ROW EXECUTE FUNCTION payment_execution_claims_guard()
    """,
    """
    CREATE TRIGGER trg_payment_execution_claims_no_truncate
    BEFORE TRUNCATE ON payment_execution_claims
    FOR EACH STATEMENT EXECUTE FUNCTION payment_execution_claims_guard()
    """,
)


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "payment_execution_claims",
        sa.Column("claim_id", sa.String(length=36), nullable=False),
        sa.Column("approval_id", sa.String(length=36), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("claim_token_hash", sa.String(length=64), nullable=False),
        sa.Column("claimed_by", sa.String(length=255), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column(
            "claimed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("claim_id", name="pk_payment_execution_claims"),
        sa.ForeignKeyConstraint(
            ["approval_id", "payload_hash"],
            ["approval_requests.approval_id", "approval_requests.payload_hash"],
            name="fk_payment_execution_claims_approval_payload_hash",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("approval_id", name="uq_payment_execution_claims_approval_id"),
        sa.UniqueConstraint(
            "claim_token_hash",
            name="uq_payment_execution_claims_claim_token_hash",
        ),
        sa.CheckConstraint(
            "status IN ('claimed')",
            name="ck_payment_execution_claims_status_allowed",
        ),
        sa.CheckConstraint(
            "length(claim_token_hash) = 64 AND claim_token_hash = lower(claim_token_hash)",
            name="ck_payment_execution_claims_token_hash_format",
        ),
        sa.CheckConstraint(
            "length(claimed_by) > 0",
            name="ck_payment_execution_claims_claimed_by_not_empty",
        ),
    )

    if _is_postgresql():
        for statement in (*POSTGRES_GUARD_FUNCTIONS, *POSTGRES_GUARD_TRIGGERS):
            op.execute(statement)


def downgrade() -> None:
    if _is_postgresql():
        op.execute(
            "DROP TRIGGER IF EXISTS trg_payment_execution_claims_no_truncate"
            " ON payment_execution_claims"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_payment_execution_claims_guard ON payment_execution_claims"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_payment_execution_claims_require_approved"
            " ON payment_execution_claims"
        )
        op.execute("DROP FUNCTION IF EXISTS payment_execution_claims_guard()")
        op.execute("DROP FUNCTION IF EXISTS payment_execution_claims_require_approved()")

    op.drop_table("payment_execution_claims")
