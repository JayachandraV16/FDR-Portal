"""Document extractions: one table recording runs of automatic field extraction

Additive only. Creates one new table, `fdr_document_extractions`, with its constraints and indexes. It
never alters or recreates any existing table. The table holds the field values an extractor found
(with confidence and page), never the document's text, and nothing in it is written back to `fdrs`.

Rollback (downgrade) drops exactly that table. Because that would discard recorded extraction runs,
downgrade refuses to run while the table contains rows unless FDR_ALLOW_DESTRUCTIVE_DOWNGRADE=1 is set
(same mechanism as 0002 to 0004).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-27
"""
import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STATUSES = "'COMPLETED', 'FAILED'"


def upgrade() -> None:
    op.create_table(
        "fdr_document_extractions",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("fdr_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("extractor", sa.String(length=120), nullable=False),
        sa.Column(
            "result",
            sa.JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql"),
            nullable=False,
        ),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(f"status IN ({STATUSES})", name=op.f("ck_fdr_document_extractions_status_valid")),
        sa.CheckConstraint(
            "(status = 'FAILED') = (error_code IS NOT NULL)",
            name=op.f("ck_fdr_document_extractions_error_code_matches_status"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["fdr_documents.id"],
            name=op.f("fk_fdr_document_extractions_document_id_fdr_documents"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["fdr_id"], ["fdrs.id"], name=op.f("fk_fdr_document_extractions_fdr_id_fdrs"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["users.id"],
            name=op.f("fk_fdr_document_extractions_requested_by_users"), ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fdr_document_extractions")),
    )
    op.create_index(op.f("ix_fdr_document_extractions_fdr_id"), "fdr_document_extractions", ["fdr_id"])
    op.create_index(
        "ix_fdr_document_extractions_document_id_requested_at", "fdr_document_extractions", ["document_id", "requested_at"]
    )


def _refuse_if_data_present() -> None:
    if context.is_offline_mode() or os.environ.get("FDR_ALLOW_DESTRUCTIVE_DOWNGRADE") == "1":
        return
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM fdr_document_extractions)")).scalar():
        raise RuntimeError(
            "Refusing to downgrade: table 'fdr_document_extractions' contains data that would be destroyed. "
            "Set FDR_ALLOW_DESTRUCTIVE_DOWNGRADE=1 only if you intend to discard it."
        )


def downgrade() -> None:
    _refuse_if_data_present()
    op.drop_index("ix_fdr_document_extractions_document_id_requested_at", table_name="fdr_document_extractions")
    op.drop_index(op.f("ix_fdr_document_extractions_fdr_id"), table_name="fdr_document_extractions")
    op.drop_table("fdr_document_extractions")
