"""Copilot answer feedback: one row per rated answer, holding no conversation content.

Deliberately stored: who rated (user id), when, the rating, an optional FIXED reason code, and the kind, source,
provider, language and page of the answer. Deliberately NOT stored: the question, the answer, any free text, any
record or document content, addresses or tokens. A client-generated `reply_id` makes a repeated submission (a double
click, a retry) land on the same row instead of adding another. Rows are deleted after 90 days by
`python -m app.chat.feedback_cleanup` (see that module).
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, SmallInteger, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin

REASONS = ("incorrect", "unclear", "incomplete", "not_relevant", "other")
KINDS = ("help", "records", "unknown")
SOURCES = ("built-in", "llm")
LANGUAGES = ("en", "hi", "mr")


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


class ChatFeedback(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "chat_feedback"
    __table_args__ = (
        CheckConstraint("rating IN (-1, 1)", name="rating_valid"),
        CheckConstraint(f"reason_code IS NULL OR reason_code IN ({_in(REASONS)})", name="reason_code_valid"),
        CheckConstraint("reason_code IS NULL OR rating = -1", name="reason_only_when_not_helpful"),
        CheckConstraint(f"reply_kind IN ({_in(KINDS)})", name="reply_kind_valid"),
        CheckConstraint(f"source IN ({_in(SOURCES)})", name="source_valid"),
        CheckConstraint(f"language IN ({_in(LANGUAGES)})", name="language_valid"),
        UniqueConstraint("user_id", "reply_id", name="uq_chat_feedback_user_id_reply_id"),
        Index("ix_chat_feedback_created_at", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 1 helpful, -1 not helpful
    reason_code: Mapped[str | None] = mapped_column(String(20))
    reply_kind: Mapped[str] = mapped_column(String(10), nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(32))
    language: Mapped[str] = mapped_column(String(2), nullable=False)
    page: Mapped[str | None] = mapped_column(String(32))
    reply_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)  # client-generated idempotency identifier
