"""Copilot feedback: one table of rated answers

Additive only. Creates one new table, `chat_feedback`, with its constraints and indexes. It never alters or
recreates any existing table.

What the table holds: the rating (helpful / not helpful), an optional fixed reason code, the kind, source, provider,
language and page of the answer, the user id, the time, and a client-generated reply id. It holds NO question text,
NO answer text, no free text, no record or document content and no addresses. A unique (user_id, reply_id) pair
makes a repeated submission idempotent. Rows older than 90 days are removed by `python -m app.chat.feedback_cleanup`.

Rollback (downgrade) drops exactly that table. Because that would discard the feedback, downgrade refuses to run
while the table contains rows unless FDR_ALLOW_DESTRUCTIVE_DOWNGRADE=1 is set (same mechanism as 0002 to 0006).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-28
"""
import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REASONS = "'incorrect', 'unclear', 'incomplete', 'not_relevant', 'other'"


def upgrade() -> None:
    op.create_table(
        "chat_feedback",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("reason_code", sa.String(length=20), nullable=True),
        sa.Column("reply_kind", sa.String(length=10), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("language", sa.String(length=2), nullable=False),
        sa.Column("page", sa.String(length=32), nullable=True),
        sa.Column("reply_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("rating IN (-1, 1)", name=op.f("ck_chat_feedback_rating_valid")),
        sa.CheckConstraint(f"reason_code IS NULL OR reason_code IN ({REASONS})", name=op.f("ck_chat_feedback_reason_code_valid")),
        sa.CheckConstraint("reason_code IS NULL OR rating = -1", name=op.f("ck_chat_feedback_reason_only_when_not_helpful")),
        sa.CheckConstraint("reply_kind IN ('help', 'records', 'unknown')", name=op.f("ck_chat_feedback_reply_kind_valid")),
        sa.CheckConstraint("source IN ('built-in', 'llm')", name=op.f("ck_chat_feedback_source_valid")),
        sa.CheckConstraint("language IN ('en', 'hi', 'mr')", name=op.f("ck_chat_feedback_language_valid")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_chat_feedback_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_feedback")),
        sa.UniqueConstraint("user_id", "reply_id", name="uq_chat_feedback_user_id_reply_id"),
    )
    op.create_index("ix_chat_feedback_created_at", "chat_feedback", ["created_at"])


def _refuse_if_data_present() -> None:
    if context.is_offline_mode() or os.environ.get("FDR_ALLOW_DESTRUCTIVE_DOWNGRADE") == "1":
        return
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM chat_feedback)")).scalar():
        raise RuntimeError(
            "Refusing to downgrade: table 'chat_feedback' contains data that would be destroyed. "
            "Set FDR_ALLOW_DESTRUCTIVE_DOWNGRADE=1 only if you intend to discard it."
        )


def downgrade() -> None:
    _refuse_if_data_present()
    op.drop_index("ix_chat_feedback_created_at", table_name="chat_feedback")
    op.drop_table("chat_feedback")
