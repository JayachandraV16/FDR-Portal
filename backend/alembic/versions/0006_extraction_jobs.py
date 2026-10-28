"""Extraction jobs: the queue for background document extraction

Additive only. Creates one new table, `fdr_extraction_jobs`, with its constraints and indexes. It never
alters or recreates any existing table (in particular it leaves `fdr_document_extractions` exactly as
0005 created it: that table keeps holding results, this one tracks the lifecycle of the request).

At most one job per document can be PENDING or RUNNING (a partial unique index), which is what makes a
repeated request idempotent and keeps two workers from running the same document at once.

Rollback (downgrade) drops exactly that table. Because that would discard the job history, downgrade
refuses to run while the table contains rows unless FDR_ALLOW_DESTRUCTIVE_DOWNGRADE=1 is set (same
mechanism as 0002 to 0005).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-27
"""
import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STATUSES = "'PENDING', 'RUNNING', 'COMPLETED', 'FAILED'"
ACTIVE = "status IN ('PENDING', 'RUNNING')"


def upgrade() -> None:
    op.create_table(
        "fdr_extraction_jobs",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("fdr_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.String(length=64), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("extraction_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(f"status IN ({STATUSES})", name=op.f("ck_fdr_extraction_jobs_status_valid")),
        sa.CheckConstraint(
            "(status = 'FAILED') = (error_code IS NOT NULL)", name=op.f("ck_fdr_extraction_jobs_error_code_matches_status")
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_fdr_extraction_jobs_attempts_nonnegative")),
        sa.ForeignKeyConstraint(
            ["document_id"], ["fdr_documents.id"],
            name=op.f("fk_fdr_extraction_jobs_document_id_fdr_documents"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["fdr_id"], ["fdrs.id"], name=op.f("fk_fdr_extraction_jobs_fdr_id_fdrs"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["users.id"], name=op.f("fk_fdr_extraction_jobs_requested_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["extraction_id"], ["fdr_document_extractions.id"],
            name=op.f("fk_fdr_extraction_jobs_extraction_id_fdr_document_extractions"), ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fdr_extraction_jobs")),
    )
    op.create_index(op.f("ix_fdr_extraction_jobs_fdr_id"), "fdr_extraction_jobs", ["fdr_id"])
    op.create_index("ix_fdr_extraction_jobs_status_requested_at", "fdr_extraction_jobs", ["status", "requested_at"])
    op.create_index(
        "ix_fdr_extraction_jobs_document_id_requested_at", "fdr_extraction_jobs", ["document_id", "requested_at"]
    )
    op.create_index(
        "uq_fdr_extraction_jobs_one_active_per_document", "fdr_extraction_jobs", ["document_id"], unique=True,
        postgresql_where=sa.text(ACTIVE), sqlite_where=sa.text(ACTIVE),
    )


def _refuse_if_data_present() -> None:
    if context.is_offline_mode() or os.environ.get("FDR_ALLOW_DESTRUCTIVE_DOWNGRADE") == "1":
        return
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM fdr_extraction_jobs)")).scalar():
        raise RuntimeError(
            "Refusing to downgrade: table 'fdr_extraction_jobs' contains data that would be destroyed. "
            "Set FDR_ALLOW_DESTRUCTIVE_DOWNGRADE=1 only if you intend to discard it."
        )


def downgrade() -> None:
    _refuse_if_data_present()
    op.drop_index("uq_fdr_extraction_jobs_one_active_per_document", table_name="fdr_extraction_jobs")
    op.drop_index("ix_fdr_extraction_jobs_document_id_requested_at", table_name="fdr_extraction_jobs")
    op.drop_index("ix_fdr_extraction_jobs_status_requested_at", table_name="fdr_extraction_jobs")
    op.drop_index(op.f("ix_fdr_extraction_jobs_fdr_id"), table_name="fdr_extraction_jobs")
    op.drop_table("fdr_extraction_jobs")
