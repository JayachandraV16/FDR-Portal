"""auth and audit: users, auth_sessions, audit_logs (+ PostgreSQL append-only triggers)

Additive only: creates three new tables and never touches banks, institutions, or their data.

Rollback (downgrade) drops exactly the objects created here. Because that would destroy user
accounts and the audit trail, downgrade refuses to run while users or audit_logs contain rows
unless FDR_ALLOW_DESTRUCTIVE_DOWNGRADE=1 is set.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-26
"""
import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROLES = "'INSTITUTION_USER', 'AICTE_REVIEWER', 'AICTE_APPROVER', 'ADMIN', 'AUDITOR'"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("institution_id", sa.Uuid(), nullable=True),
        sa.Column("must_change_password", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("failed_login_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"role IN ({ROLES})", name=op.f("ck_users_role_valid")),
        sa.CheckConstraint(
            "(role = 'INSTITUTION_USER' AND institution_id IS NOT NULL)"
            " OR (role <> 'INSTITUTION_USER' AND institution_id IS NULL)",
            name=op.f("ck_users_institution_matches_role"),
        ),
        sa.CheckConstraint("failed_login_count >= 0", name=op.f("ck_users_failed_login_count_nonneg")),
        sa.ForeignKeyConstraint(
            ["institution_id"], ["institutions.id"],
            name=op.f("fk_users_institution_id_institutions"), ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_index(op.f("ix_users_institution_id"), "users", ["institution_id"])

    op.create_table(
        "auth_sessions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_auth_sessions_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_auth_sessions_token_hash")),
    )
    op.create_index(op.f("ix_auth_sessions_user_id"), "auth_sessions", ["user_id"])

    op.create_table(
        "audit_logs",
        sa.Column("seq", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_role", sa.String(length=30), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=True),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("details", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=True),
        sa.Column("prev_hash", sa.String(length=64), nullable=True),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('success', 'failure', 'denied')", name=op.f("ck_audit_logs_outcome_valid")
        ),
        # No ON DELETE action: SET NULL would be an UPDATE that the immutability trigger rejects.
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name=op.f("fk_audit_logs_actor_user_id_users")
        ),
        sa.PrimaryKeyConstraint("seq", name=op.f("pk_audit_logs")),
        sa.UniqueConstraint("hash", name=op.f("uq_audit_logs_hash")),
    )
    op.create_index(op.f("ix_audit_logs_occurred_at"), "audit_logs", ["occurred_at"])
    op.create_index(op.f("ix_audit_logs_actor_user_id"), "audit_logs", ["actor_user_id"])
    op.create_index(op.f("ix_audit_logs_action"), "audit_logs", ["action"])
    op.create_index("ix_audit_logs_entity", "audit_logs", ["entity_type", "entity_id"])

    if _is_postgres():
        op.execute(
            """
            CREATE FUNCTION audit_logs_block_modification() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'audit_logs is append-only: % is not permitted', TG_OP
                    USING ERRCODE = 'insufficient_privilege';
            END;
            $$
            """
        )
        op.execute(
            "CREATE TRIGGER audit_logs_no_update_delete BEFORE UPDATE OR DELETE ON audit_logs "
            "FOR EACH ROW EXECUTE FUNCTION audit_logs_block_modification()"
        )
        op.execute(
            "CREATE TRIGGER audit_logs_no_truncate BEFORE TRUNCATE ON audit_logs "
            "FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_block_modification()"
        )


def _refuse_if_data_present() -> None:
    if context.is_offline_mode() or os.environ.get("FDR_ALLOW_DESTRUCTIVE_DOWNGRADE") == "1":
        return
    bind = op.get_bind()
    for table in ("audit_logs", "users"):
        if bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar():
            raise RuntimeError(
                f"Refusing to downgrade: table '{table}' contains data that would be destroyed. "
                "Set FDR_ALLOW_DESTRUCTIVE_DOWNGRADE=1 only if you intend to discard it."
            )


def downgrade() -> None:
    _refuse_if_data_present()
    if _is_postgres():
        op.execute("DROP TRIGGER IF EXISTS audit_logs_no_truncate ON audit_logs")
        op.execute("DROP TRIGGER IF EXISTS audit_logs_no_update_delete ON audit_logs")
        op.execute("DROP FUNCTION IF EXISTS audit_logs_block_modification()")
    op.drop_table("audit_logs")
    op.drop_table("auth_sessions")
    op.drop_table("users")
