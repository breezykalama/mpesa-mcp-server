"""Create durable approval request and reviewer decision tables.

Schema foundation only: nothing in the runtime reads or writes these tables yet.

On PostgreSQL the migration also installs guard triggers:

* ``approval_requests``: the payment payload, payload hash, policy snapshot, quorum and
  other creation-time fields are immutable; a resolved (non-pending) request can never
  change again; every update must advance ``version`` by exactly one; rows cannot be
  deleted or truncated.
* ``approval_decisions``: append-only; rows cannot be updated, deleted or truncated.
* JSONB shape checks: payloads and policy snapshots must be objects and reviewer roles
  an array.

Other dialects (SQLite for local development) get the tables and declarative
constraints but not the triggers or JSONB shape checks.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_durable_approvals"
down_revision = "0007_audit_operator_roles"
branch_labels = None
depends_on = None

DOCUMENT_JSON = sa.JSON(none_as_null=True).with_variant(
    postgresql.JSONB(none_as_null=True), "postgresql"
)

POSTGRES_DOCUMENT_SHAPE_CHECKS = (
    (
        "approval_requests",
        "ck_approval_requests_payment_payload_object",
        "jsonb_typeof(payment_payload) = 'object'",
    ),
    (
        "approval_requests",
        "ck_approval_requests_policy_snapshot_object",
        "jsonb_typeof(policy_snapshot) = 'object'",
    ),
    (
        "approval_decisions",
        "ck_approval_decisions_reviewer_roles_array",
        "jsonb_typeof(reviewer_roles) = 'array'",
    ),
)
"""JSONB document shape checks (PostgreSQL only; rejects JSON ``null`` and scalars)."""

POSTGRES_GUARD_FUNCTIONS = (
    """
    CREATE FUNCTION approval_requests_guard() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' OR TG_OP = 'TRUNCATE' THEN
            RAISE EXCEPTION 'approval_requests rows cannot be deleted'
                USING ERRCODE = 'restrict_violation';
        END IF;

        IF OLD.status <> 'pending' THEN
            RAISE EXCEPTION 'approval request % is resolved and cannot change',
                OLD.approval_id
                USING ERRCODE = 'restrict_violation';
        END IF;

        IF NEW.approval_id IS DISTINCT FROM OLD.approval_id
            OR NEW.action IS DISTINCT FROM OLD.action
            OR NEW.reason IS DISTINCT FROM OLD.reason
            OR NEW.payment_payload IS DISTINCT FROM OLD.payment_payload
            OR NEW.payload_hash_algorithm IS DISTINCT FROM OLD.payload_hash_algorithm
            OR NEW.payload_hash IS DISTINCT FROM OLD.payload_hash
            OR NEW.policy_version IS DISTINCT FROM OLD.policy_version
            OR NEW.policy_snapshot IS DISTINCT FROM OLD.policy_snapshot
            OR NEW.required_reviewers IS DISTINCT FROM OLD.required_reviewers
            OR NEW.requested_by IS DISTINCT FROM OLD.requested_by
            OR NEW.correlation_id IS DISTINCT FROM OLD.correlation_id
            OR NEW.created_at IS DISTINCT FROM OLD.created_at
            OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
        THEN
            RAISE EXCEPTION 'approval request % immutable fields cannot change',
                OLD.approval_id
                USING ERRCODE = 'restrict_violation';
        END IF;

        IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
            RAISE EXCEPTION 'approval request % update must advance version by one',
                OLD.approval_id
                USING ERRCODE = 'restrict_violation';
        END IF;

        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE FUNCTION approval_decisions_append_only() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'approval_decisions is append-only'
            USING ERRCODE = 'restrict_violation';
    END;
    $$
    """,
)

POSTGRES_GUARD_TRIGGERS = (
    """
    CREATE TRIGGER trg_approval_requests_guard
    BEFORE UPDATE OR DELETE ON approval_requests
    FOR EACH ROW EXECUTE FUNCTION approval_requests_guard()
    """,
    """
    CREATE TRIGGER trg_approval_requests_no_truncate
    BEFORE TRUNCATE ON approval_requests
    FOR EACH STATEMENT EXECUTE FUNCTION approval_requests_guard()
    """,
    """
    CREATE TRIGGER trg_approval_decisions_append_only
    BEFORE UPDATE OR DELETE ON approval_decisions
    FOR EACH ROW EXECUTE FUNCTION approval_decisions_append_only()
    """,
    """
    CREATE TRIGGER trg_approval_decisions_no_truncate
    BEFORE TRUNCATE ON approval_decisions
    FOR EACH STATEMENT EXECUTE FUNCTION approval_decisions_append_only()
    """,
)


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "approval_requests",
        sa.Column("approval_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("payment_payload", DOCUMENT_JSON, nullable=False),
        sa.Column("payload_hash_algorithm", sa.String(length=16), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("policy_snapshot", DOCUMENT_JSON, nullable=False),
        sa.Column("required_reviewers", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.String(length=255), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("approval_id", name="pk_approval_requests"),
        sa.UniqueConstraint(
            "approval_id",
            "payload_hash",
            name="uq_approval_requests_approval_id_payload_hash",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired')",
            name="ck_approval_requests_status_allowed",
        ),
        sa.CheckConstraint(
            "required_reviewers BETWEEN 1 AND 10",
            name="ck_approval_requests_required_reviewers_range",
        ),
        sa.CheckConstraint(
            "payload_hash_algorithm IN ('sha256')",
            name="ck_approval_requests_payload_hash_algorithm_allowed",
        ),
        sa.CheckConstraint(
            "length(payload_hash) = 64 AND payload_hash = lower(payload_hash)",
            name="ck_approval_requests_payload_hash_format",
        ),
        sa.CheckConstraint(
            "length(action) > 0",
            name="ck_approval_requests_action_not_empty",
        ),
        sa.CheckConstraint(
            "length(policy_version) > 0",
            name="ck_approval_requests_policy_version_not_empty",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_approval_requests_expiry_after_creation",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND resolved_at IS NULL)"
            " OR (status <> 'pending' AND resolved_at IS NOT NULL)",
            name="ck_approval_requests_resolution_consistent",
        ),
        sa.CheckConstraint("version >= 1", name="ck_approval_requests_version_positive"),
    )
    op.create_index(
        "ix_approval_requests_status_expires_at",
        "approval_requests",
        ["status", "expires_at"],
    )
    op.create_index("ix_approval_requests_created_at", "approval_requests", ["created_at"])
    op.create_index(
        "ix_approval_requests_correlation_id",
        "approval_requests",
        ["correlation_id"],
    )

    op.create_table(
        "approval_decisions",
        sa.Column("decision_id", sa.String(length=36), nullable=False),
        sa.Column("approval_id", sa.String(length=36), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reviewer_issuer", sa.String(length=512), nullable=False),
        sa.Column("reviewer_subject", sa.String(length=255), nullable=False),
        sa.Column("reviewer_email", sa.String(length=320), nullable=True),
        sa.Column("reviewer_display_name", sa.String(length=255), nullable=True),
        sa.Column("reviewer_roles", DOCUMENT_JSON, nullable=False),
        sa.Column("authentication_method", sa.String(length=32), nullable=False),
        sa.Column("principal_type", sa.String(length=16), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("decision_id", name="pk_approval_decisions"),
        sa.ForeignKeyConstraint(
            ["approval_id", "payload_hash"],
            ["approval_requests.approval_id", "approval_requests.payload_hash"],
            name="fk_approval_decisions_approval_payload_hash",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "approval_id",
            "reviewer_issuer",
            "reviewer_subject",
            name="uq_approval_decisions_reviewer",
        ),
        sa.CheckConstraint(
            "decision IN ('approve', 'reject')",
            name="ck_approval_decisions_decision_allowed",
        ),
        sa.CheckConstraint(
            "length(reviewer_issuer) > 0",
            name="ck_approval_decisions_reviewer_issuer_not_empty",
        ),
        sa.CheckConstraint(
            "length(reviewer_subject) > 0",
            name="ck_approval_decisions_reviewer_subject_not_empty",
        ),
        sa.CheckConstraint(
            "authentication_method IN"
            " ('oidc_jwks', 'oidc_development', 'static_token', 'auth_disabled')",
            name="ck_approval_decisions_authentication_method_allowed",
        ),
        sa.CheckConstraint(
            "principal_type IN ('human', 'application', 'unknown')",
            name="ck_approval_decisions_principal_type_allowed",
        ),
    )
    op.create_index(
        "ix_approval_decisions_reviewer",
        "approval_decisions",
        ["reviewer_issuer", "reviewer_subject"],
    )

    if _is_postgresql():
        for table, name, condition in POSTGRES_DOCUMENT_SHAPE_CHECKS:
            op.create_check_constraint(name, table, condition)
        for statement in (*POSTGRES_GUARD_FUNCTIONS, *POSTGRES_GUARD_TRIGGERS):
            op.execute(statement)


def downgrade() -> None:
    if _is_postgresql():
        op.execute(
            "DROP TRIGGER IF EXISTS trg_approval_decisions_no_truncate ON approval_decisions"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_approval_decisions_append_only ON approval_decisions"
        )
        op.execute("DROP TRIGGER IF EXISTS trg_approval_requests_no_truncate ON approval_requests")
        op.execute("DROP TRIGGER IF EXISTS trg_approval_requests_guard ON approval_requests")
        op.execute("DROP FUNCTION IF EXISTS approval_decisions_append_only()")
        op.execute("DROP FUNCTION IF EXISTS approval_requests_guard()")

    op.drop_index("ix_approval_decisions_reviewer", table_name="approval_decisions")
    op.drop_table("approval_decisions")
    op.drop_index("ix_approval_requests_correlation_id", table_name="approval_requests")
    op.drop_index("ix_approval_requests_created_at", table_name="approval_requests")
    op.drop_index("ix_approval_requests_status_expires_at", table_name="approval_requests")
    op.drop_table("approval_requests")
