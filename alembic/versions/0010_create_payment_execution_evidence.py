"""Create durable payment execution attempts and append-only outcome evidence (E2.1).

``payment_execution_attempts``: at most one attempt per E1 execution claim, recorded by
the claim owner. ``payment_execution_evidence``: append-only facts observed about that
attempt, ordered by a per-attempt sequence.

Evidence never proves more than it observed:

* ``submission_started`` is written *before* any provider call and means the payment
  may have been sent, not that the provider received it;
* ``outcome_unknown`` records that the worker could not tell what happened;
* ``provider_response_observed`` records a provider response as reported, with its
  source, and is always ``verification_level = 'unverified'`` in this slice. No trusted
  provider-verification path exists yet, so no verified outcome can be stored.

Integrity rules (all dialects): every attempt and evidence row is bound by composite
foreign keys to its claim, approval and approved payload hash; evidence type, source
and field combinations are checked; observation keys and sequences are unique per
attempt.

On PostgreSQL the migration also installs triggers that keep attempts and evidence
append-only (no UPDATE, DELETE or TRUNCATE), require an attempt's ``recorded_by`` to be
the claim owner, and require ``submission_started`` before any other evidence.

The E1 claims table gains a unique key on ``(claim_id, approval_id, payload_hash)`` so
attempts can reference the full claim identity; E1 uniqueness and ownership rules are
unchanged.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_payment_execution_evidence"
down_revision = "0009_payment_execution_claims"
branch_labels = None
depends_on = None

POSTGRES_GUARD_FUNCTIONS = (
    """
    CREATE FUNCTION payment_execution_append_only() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
            USING ERRCODE = 'restrict_violation';
    END;
    $$
    """,
    """
    CREATE FUNCTION payment_execution_attempts_require_owner() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM payment_execution_claims
            WHERE claim_id = NEW.claim_id AND claimed_by = NEW.recorded_by
        ) THEN
            RAISE EXCEPTION 'execution attempt for claim % must be recorded by its owner',
                NEW.claim_id
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE FUNCTION payment_execution_evidence_require_submission() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.evidence_type <> 'submission_started' AND NOT EXISTS (
            SELECT 1 FROM payment_execution_evidence
            WHERE attempt_id = NEW.attempt_id AND evidence_type = 'submission_started'
        ) THEN
            RAISE EXCEPTION 'attempt % has no submission_started evidence',
                NEW.attempt_id
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
)

POSTGRES_GUARD_TRIGGERS = (
    """
    CREATE TRIGGER trg_payment_execution_attempts_require_owner
    BEFORE INSERT ON payment_execution_attempts
    FOR EACH ROW EXECUTE FUNCTION payment_execution_attempts_require_owner()
    """,
    """
    CREATE TRIGGER trg_payment_execution_attempts_append_only
    BEFORE UPDATE OR DELETE ON payment_execution_attempts
    FOR EACH ROW EXECUTE FUNCTION payment_execution_append_only()
    """,
    """
    CREATE TRIGGER trg_payment_execution_attempts_no_truncate
    BEFORE TRUNCATE ON payment_execution_attempts
    FOR EACH STATEMENT EXECUTE FUNCTION payment_execution_append_only()
    """,
    """
    CREATE TRIGGER trg_payment_execution_evidence_require_submission
    BEFORE INSERT ON payment_execution_evidence
    FOR EACH ROW EXECUTE FUNCTION payment_execution_evidence_require_submission()
    """,
    """
    CREATE TRIGGER trg_payment_execution_evidence_append_only
    BEFORE UPDATE OR DELETE ON payment_execution_evidence
    FOR EACH ROW EXECUTE FUNCTION payment_execution_append_only()
    """,
    """
    CREATE TRIGGER trg_payment_execution_evidence_no_truncate
    BEFORE TRUNCATE ON payment_execution_evidence
    FOR EACH STATEMENT EXECUTE FUNCTION payment_execution_append_only()
    """,
)

CLAIM_IDENTITY_KEY = "uq_payment_execution_claims_identity"


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    with op.batch_alter_table("payment_execution_claims") as batch:
        batch.create_unique_constraint(
            CLAIM_IDENTITY_KEY, ["claim_id", "approval_id", "payload_hash"]
        )

    op.create_table(
        "payment_execution_attempts",
        sa.Column("attempt_id", sa.String(length=36), nullable=False),
        sa.Column("claim_id", sa.String(length=36), nullable=False),
        sa.Column("approval_id", sa.String(length=36), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("rail", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("recorded_by", sa.String(length=255), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("attempt_id", name="pk_payment_execution_attempts"),
        sa.ForeignKeyConstraint(
            ["claim_id", "approval_id", "payload_hash"],
            [
                "payment_execution_claims.claim_id",
                "payment_execution_claims.approval_id",
                "payment_execution_claims.payload_hash",
            ],
            name="fk_payment_execution_attempts_claim_identity",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("claim_id", name="uq_payment_execution_attempts_claim_id"),
        sa.UniqueConstraint(
            "attempt_id",
            "claim_id",
            "approval_id",
            "payload_hash",
            name="uq_payment_execution_attempts_identity",
        ),
        sa.CheckConstraint(
            "length(provider) > 0 AND length(rail) > 0",
            name="ck_payment_execution_attempts_provider_not_empty",
        ),
        sa.CheckConstraint(
            "length(recorded_by) > 0",
            name="ck_payment_execution_attempts_recorded_by_not_empty",
        ),
    )

    op.create_table(
        "payment_execution_evidence",
        sa.Column("evidence_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=False),
        sa.Column("claim_id", sa.String(length=36), nullable=False),
        sa.Column("approval_id", sa.String(length=36), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("evidence_type", sa.String(length=32), nullable=False),
        sa.Column("evidence_source", sa.String(length=32), nullable=False),
        sa.Column("verification_level", sa.String(length=16), nullable=False),
        sa.Column("observation_key", sa.String(length=200), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("uncertainty_reason", sa.String(length=32), nullable=True),
        sa.Column("result_code", sa.String(length=32), nullable=True),
        sa.Column("result_description", sa.String(length=255), nullable=True),
        sa.Column("checkout_request_id", sa.String(length=128), nullable=True),
        sa.Column("merchant_request_id", sa.String(length=128), nullable=True),
        sa.Column("provider_transaction_id", sa.String(length=128), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("evidence_id", name="pk_payment_execution_evidence"),
        sa.ForeignKeyConstraint(
            ["attempt_id", "claim_id", "approval_id", "payload_hash"],
            [
                "payment_execution_attempts.attempt_id",
                "payment_execution_attempts.claim_id",
                "payment_execution_attempts.approval_id",
                "payment_execution_attempts.payload_hash",
            ],
            name="fk_payment_execution_evidence_attempt_identity",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "attempt_id", "sequence", name="uq_payment_execution_evidence_sequence"
        ),
        sa.UniqueConstraint(
            "attempt_id",
            "observation_key",
            name="uq_payment_execution_evidence_observation_key",
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_payment_execution_evidence_sequence"),
        sa.CheckConstraint(
            "verification_level IN ('unverified')",
            name="ck_payment_execution_evidence_verification_level",
        ),
        sa.CheckConstraint(
            "(evidence_type = 'submission_started' AND evidence_source = 'execution_worker'"
            "  AND uncertainty_reason IS NULL AND result_code IS NULL)"
            " OR (evidence_type = 'outcome_unknown' AND evidence_source = 'execution_worker'"
            # IS NOT NULL keeps the branch FALSE (not UNKNOWN) for a NULL reason.
            "  AND uncertainty_reason IS NOT NULL AND uncertainty_reason IN"
            "   ('timeout', 'connection_error', 'unexpected_error', 'worker_interrupted')"
            "  AND result_code IS NULL)"
            " OR (evidence_type = 'provider_response_observed'"
            "  AND evidence_source IN"
            "   ('provider_initiation_response', 'provider_callback', 'provider_status_query')"
            "  AND uncertainty_reason IS NULL AND result_code IS NOT NULL)",
            name="ck_payment_execution_evidence_type_source",
        ),
        sa.CheckConstraint(
            "length(evidence_hash) = 64 AND evidence_hash = lower(evidence_hash)",
            name="ck_payment_execution_evidence_hash_format",
        ),
        sa.CheckConstraint(
            "length(observation_key) > 0",
            name="ck_payment_execution_evidence_observation_key_not_empty",
        ),
    )
    op.create_index(
        "ix_payment_execution_evidence_checkout_request_id",
        "payment_execution_evidence",
        ["checkout_request_id"],
    )

    if _is_postgresql():
        for statement in (*POSTGRES_GUARD_FUNCTIONS, *POSTGRES_GUARD_TRIGGERS):
            op.execute(statement)


def downgrade() -> None:
    if _is_postgresql():
        for table, trigger in (
            ("payment_execution_evidence", "trg_payment_execution_evidence_no_truncate"),
            ("payment_execution_evidence", "trg_payment_execution_evidence_append_only"),
            ("payment_execution_evidence", "trg_payment_execution_evidence_require_submission"),
            ("payment_execution_attempts", "trg_payment_execution_attempts_no_truncate"),
            ("payment_execution_attempts", "trg_payment_execution_attempts_append_only"),
            ("payment_execution_attempts", "trg_payment_execution_attempts_require_owner"),
        ):
            op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        op.execute("DROP FUNCTION IF EXISTS payment_execution_evidence_require_submission()")
        op.execute("DROP FUNCTION IF EXISTS payment_execution_attempts_require_owner()")
        op.execute("DROP FUNCTION IF EXISTS payment_execution_append_only()")

    op.drop_index(
        "ix_payment_execution_evidence_checkout_request_id",
        table_name="payment_execution_evidence",
    )
    op.drop_table("payment_execution_evidence")
    op.drop_table("payment_execution_attempts")
    with op.batch_alter_table("payment_execution_claims") as batch:
        batch.drop_constraint(CLAIM_IDENTITY_KEY, type_="unique")
