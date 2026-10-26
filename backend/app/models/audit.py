import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Autoincrementing integer primary key on SQLite (used by tests), BIGINT identity on PostgreSQL.
_SEQ = BigInteger().with_variant(Integer(), "sqlite")
_JSON = JSON().with_variant(JSONB(), "postgresql")


class AuditLog(Base):
    """Append-only security audit trail.

    Tamper-evidence, not absolute immutability: PostgreSQL triggers (migration 0002) reject
    UPDATE/DELETE/TRUNCATE, and each row's hash covers the previous row's hash. A sufficiently
    privileged database administrator can still disable triggers and rewrite the chain.
    `details` is written through an allow-list scrubber (Stage 2) and never holds secrets.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint("outcome IN ('success', 'failure', 'denied')", name="outcome_valid"),
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
    )

    # Monotonic sequence number; defines the order of the hash chain.
    seq: Mapped[int] = mapped_column(_SEQ, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # No ON DELETE action: setting NULL would be an UPDATE the immutability trigger rejects.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), index=True
    )
    actor_role: Mapped[str | None] = mapped_column(String(30))
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    request_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any] | None] = mapped_column(_JSON)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
