"""FDR documents: metadata table for uploaded files

Additive only. Creates one new table, `fdr_documents`, with its constraints and indexes. It never
alters or recreates any existing table. The document bytes are not stored in the database; the table
holds metadata and a private, server-generated storage key.

Rollback (downgrade) drops exactly that table. Because that would discard the record of which files
belong to which FDR, downgrade refuses to run while the table contains rows unless
FDR_ALLOW_DESTRUCTIVE_DOWNGRADE=1 is set (same mechanism as 0002 and 0003). Files already written to
the storage directory are never touched by this migration.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-27
"""
import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CATEGORIES = "'FDR_CERTIFICATE', 'SECURITY_DEPOSIT_PROOF', 'SUPPORTING_LETTER', 'OTHER'"
STATUSES = "'ACTIVE', 'DELETED'"
CONTENT_TYPES = "'application/pdf', 'image/jpeg', 'image/png'"


def upgrade() -> None:
    op.create_table(
        "fdr_documents",
        sa.Column("fdr_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("storage_key", sa.String(length=32), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'ACTIVE'"), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_by", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(f"category IN ({CATEGORIES})", name=op.f("ck_fdr_documents_category_valid")),
        sa.CheckConstraint(f"status IN ({STATUSES})", name=op.f("ck_fdr_documents_status_valid")),
        sa.CheckConstraint(f"content_type IN ({CONTENT_TYPES})", name=op.f("ck_fdr_documents_content_type_valid")),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_fdr_documents_size_positive")),
        sa.CheckConstraint("length(sha256) = 64", name=op.f("ck_fdr_documents_sha256_length")),
        sa.CheckConstraint("length(storage_key) = 32", name=op.f("ck_fdr_documents_storage_key_length")),
        sa.CheckConstraint("length(original_filename) > 0", name=op.f("ck_fdr_documents_original_filename_nonempty")),
        sa.CheckConstraint(
            "(status = 'DELETED') = (deleted_at IS NOT NULL)", name=op.f("ck_fdr_documents_deleted_at_matches_status")
        ),
        sa.ForeignKeyConstraint(
            ["fdr_id"], ["fdrs.id"], name=op.f("fk_fdr_documents_fdr_id_fdrs"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], ["users.id"], name=op.f("fk_fdr_documents_uploaded_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["deleted_by"], ["users.id"], name=op.f("fk_fdr_documents_deleted_by_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fdr_documents")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_fdr_documents_storage_key")),
    )
    op.create_index("ix_fdr_documents_fdr_id_status", "fdr_documents", ["fdr_id", "status"])


def _refuse_if_data_present() -> None:
    if context.is_offline_mode() or os.environ.get("FDR_ALLOW_DESTRUCTIVE_DOWNGRADE") == "1":
        return
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM fdr_documents)")).scalar():
        raise RuntimeError(
            "Refusing to downgrade: table 'fdr_documents' contains data that would be destroyed. "
            "Set FDR_ALLOW_DESTRUCTIVE_DOWNGRADE=1 only if you intend to discard it."
        )


def downgrade() -> None:
    _refuse_if_data_present()
    op.drop_index("ix_fdr_documents_fdr_id_status", table_name="fdr_documents")
    op.drop_table("fdr_documents")
