"""FDR core: fdrs, fdr_status_history, fdr_revisions (+ PostgreSQL append-only triggers)

Additive only. Creates three new tables, their constraints/indexes and one new trigger function.
It never alters or recreates banks, institutions, users, auth_sessions or audit_logs, and it does
not touch the existing audit_logs_block_modification() function or its triggers.

Rollback (downgrade) drops exactly the objects created here. Because that would destroy FDR
records and their history, downgrade refuses to run while any of the three tables contains rows
unless FDR_ALLOW_DESTRUCTIVE_DOWNGRADE=1 is set (same mechanism as 0002).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-26
"""
import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STATUSES = "'DRAFT', 'SUBMITTED', 'CORRECTION_REQUESTED', 'ACCEPTED', 'REJECTED', 'WITHDRAWN'"
ACTIVE_STATUSES = "'SUBMITTED', 'CORRECTION_REQUESTED', 'ACCEPTED'"
ROLES = "'INSTITUTION_USER', 'AICTE_REVIEWER', 'AICTE_APPROVER', 'ADMIN', 'AUDITOR'"

TABLES_GUARDED_ON_DOWNGRADE = ("fdr_revisions", "fdr_status_history", "fdrs")


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "fdrs",
        sa.Column("institution_id", sa.Uuid(), nullable=False),
        sa.Column("bank_id", sa.Uuid(), nullable=False),
        sa.Column("fdr_number", sa.String(length=64), nullable=False),
        sa.Column("fdr_number_normalized", sa.String(length=64), nullable=False),
        sa.Column("branch_name", sa.String(length=200), nullable=True),
        sa.Column("depositor_name", sa.String(length=300), nullable=True),
        sa.Column("purpose", sa.String(length=200), nullable=True),
        sa.Column("principal_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("interest_rate_pct", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("maturity_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=30), server_default=sa.text("'DRAFT'"), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("submitted_by", sa.Uuid(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"status IN ({STATUSES})", name=op.f("ck_fdrs_status_valid")),
        sa.CheckConstraint("principal_amount > 0", name=op.f("ck_fdrs_principal_positive")),
        sa.CheckConstraint(
            "interest_rate_pct IS NULL OR (interest_rate_pct >= 0 AND interest_rate_pct <= 100)",
            name=op.f("ck_fdrs_interest_rate_range"),
        ),
        sa.CheckConstraint("maturity_date > issue_date", name=op.f("ck_fdrs_maturity_after_issue")),
        sa.CheckConstraint("length(fdr_number) > 0", name=op.f("ck_fdrs_fdr_number_nonempty")),
        sa.CheckConstraint(
            "length(fdr_number_normalized) > 0", name=op.f("ck_fdrs_fdr_number_normalized_nonempty")
        ),
        sa.CheckConstraint("row_version >= 1", name=op.f("ck_fdrs_row_version_positive")),
        sa.ForeignKeyConstraint(
            ["institution_id"], ["institutions.id"],
            name=op.f("fk_fdrs_institution_id_institutions"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["bank_id"], ["banks.id"], name=op.f("fk_fdrs_bank_id_banks"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_fdrs_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by"], ["users.id"], name=op.f("fk_fdrs_submitted_by_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fdrs")),
    )
    op.create_index(op.f("ix_fdrs_bank_id"), "fdrs", ["bank_id"])
    op.create_index(op.f("ix_fdrs_maturity_date"), "fdrs", ["maturity_date"])
    op.create_index(op.f("ix_fdrs_status"), "fdrs", ["status"])
    op.create_index("ix_fdrs_institution_id_status", "fdrs", ["institution_id", "status"])
    # One live record per instrument number per bank, across all institutions.
    op.create_index(
        "uq_fdrs_active_bank_fdr_number",
        "fdrs",
        ["bank_id", "fdr_number_normalized"],
        unique=True,
        postgresql_where=sa.text(f"status IN ({ACTIVE_STATUSES})"),
        sqlite_where=sa.text(f"status IN ({ACTIVE_STATUSES})"),
    )

    op.create_table(
        "fdr_status_history",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False),
        sa.Column("fdr_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(length=30), nullable=True),
        sa.Column("to_status", sa.String(length=30), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("actor_role", sa.String(length=30), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"to_status IN ({STATUSES})", name=op.f("ck_fdr_status_history_to_status_valid")),
        sa.CheckConstraint(
            f"from_status IS NULL OR from_status IN ({STATUSES})",
            name=op.f("ck_fdr_status_history_from_status_valid"),
        ),
        sa.CheckConstraint(
            "from_status IS NULL OR from_status <> to_status",
            name=op.f("ck_fdr_status_history_status_changed"),
        ),
        sa.CheckConstraint(f"actor_role IN ({ROLES})", name=op.f("ck_fdr_status_history_actor_role_valid")),
        sa.CheckConstraint(
            "comment IS NULL OR length(comment) <= 2000", name=op.f("ck_fdr_status_history_comment_length")
        ),
        sa.ForeignKeyConstraint(
            ["fdr_id"], ["fdrs.id"], name=op.f("fk_fdr_status_history_fdr_id_fdrs"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"],
            name=op.f("fk_fdr_status_history_actor_user_id_users"), ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fdr_status_history")),
    )
    op.create_index(
        "ix_fdr_status_history_fdr_id_occurred_at", "fdr_status_history", ["fdr_id", "occurred_at"]
    )

    op.create_table(
        "fdr_revisions",
        sa.Column("fdr_id", sa.Uuid(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        sa.Column("submitted_by", sa.Uuid(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("revision_no >= 1", name=op.f("ck_fdr_revisions_revision_no_positive")),
        sa.ForeignKeyConstraint(
            ["fdr_id"], ["fdrs.id"], name=op.f("fk_fdr_revisions_fdr_id_fdrs"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by"], ["users.id"],
            name=op.f("fk_fdr_revisions_submitted_by_users"), ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fdr_revisions")),
        sa.UniqueConstraint("fdr_id", "revision_no", name="uq_fdr_revisions_fdr_id_revision_no"),
    )

    if _is_postgres():
        # New generic function; the audit_logs one from 0002 is deliberately left alone.
        op.execute(
            """
            CREATE FUNCTION append_only_block_modification() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION '% is append-only: % is not permitted', TG_TABLE_NAME, TG_OP
                    USING ERRCODE = 'insufficient_privilege';
            END;
            $$
            """
        )
        for table in ("fdr_status_history", "fdr_revisions"):
            op.execute(
                f"CREATE TRIGGER {table}_no_update_delete BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION append_only_block_modification()"
            )
            op.execute(
                f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
                "FOR EACH STATEMENT EXECUTE FUNCTION append_only_block_modification()"
            )


def _refuse_if_data_present() -> None:
    if context.is_offline_mode() or os.environ.get("FDR_ALLOW_DESTRUCTIVE_DOWNGRADE") == "1":
        return
    bind = op.get_bind()
    for table in TABLES_GUARDED_ON_DOWNGRADE:
        if bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar():
            raise RuntimeError(
                f"Refusing to downgrade: table '{table}' contains data that would be destroyed. "
                "Set FDR_ALLOW_DESTRUCTIVE_DOWNGRADE=1 only if you intend to discard it."
            )


def downgrade() -> None:
    _refuse_if_data_present()
    if _is_postgres():
        for table in ("fdr_revisions", "fdr_status_history"):
            op.execute(f"DROP TRIGGER IF EXISTS {table}_no_truncate ON {table}")
            op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update_delete ON {table}")
        op.execute("DROP FUNCTION IF EXISTS append_only_block_modification()")
    op.drop_table("fdr_revisions")
    op.drop_table("fdr_status_history")
    op.drop_table("fdrs")
