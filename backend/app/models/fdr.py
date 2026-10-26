"""FDR / security-deposit domain models (Stage 5a: schema only, no workflow logic yet).

Provisional product assumptions, revisable once the official PS 26265 text is available:
  * Amounts are in INR (ASSUMED_CURRENCY). There is deliberately no currency column; supporting
    more currencies later is one additive migration plus a snapshot_version bump.
  * Status names are a proposed design, not official AICTE terminology. ACCEPTED means "accepted
    after human review of the submitted information". It does NOT mean bank-verified.
  * Fields that a submission will require (to be finalised in Stage 5d) are nullable here wherever
    a draft may legitimately omit them; only what every record needs is NOT NULL.
"""

import enum
import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.user import Role

ASSUMED_CURRENCY = "INR"
SNAPSHOT_VERSION = 1

_HISTORY_ID = BigInteger().with_variant(Integer(), "sqlite")  # autoincrement on SQLite (tests)
# none_as_null: Python None must become SQL NULL (so NOT NULL bites), never the JSON value null.
_JSON = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


class FdrStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    CORRECTION_REQUESTED = "CORRECTION_REQUESTED"
    ACCEPTED = "ACCEPTED"  # accepted after human review; NOT bank-verified
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


# States in which an instrument number is "live" and must be unique per bank. DRAFT, REJECTED and
# WITHDRAWN records never block anyone, so a mistyped draft cannot squat on a number.
ACTIVE_STATUSES = (FdrStatus.SUBMITTED, FdrStatus.CORRECTION_REQUESTED, FdrStatus.ACCEPTED)


def _in_list(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


_ALL_STATUSES = _in_list(s.value for s in FdrStatus)
_ACTIVE_STATUSES = _in_list(s.value for s in ACTIVE_STATUSES)
_ALL_ROLES = _in_list(r.value for r in Role)


def normalize_fdr_number(raw: str) -> str:
    """Duplicate-detection key: all whitespace removed, upper-cased. Hyphens and other punctuation
    are kept because they may be significant. Raises ValueError if nothing is left."""
    normalized = re.sub(r"\s+", "", raw).upper()
    if not normalized:
        raise ValueError("FDR number is empty")
    return normalized


class FDR(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "fdrs"
    __table_args__ = (
        CheckConstraint(f"status IN ({_ALL_STATUSES})", name="status_valid"),
        CheckConstraint("principal_amount > 0", name="principal_positive"),
        CheckConstraint(
            "interest_rate_pct IS NULL OR (interest_rate_pct >= 0 AND interest_rate_pct <= 100)",
            name="interest_rate_range",
        ),
        CheckConstraint("maturity_date > issue_date", name="maturity_after_issue"),
        CheckConstraint("length(fdr_number) > 0", name="fdr_number_nonempty"),
        CheckConstraint("length(fdr_number_normalized) > 0", name="fdr_number_normalized_nonempty"),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        # One live record per instrument number per bank, across ALL institutions.
        Index(
            "uq_fdrs_active_bank_fdr_number",
            "bank_id",
            "fdr_number_normalized",
            unique=True,
            postgresql_where=text(f"status IN ({_ACTIVE_STATUSES})"),
            sqlite_where=text(f"status IN ({_ACTIVE_STATUSES})"),
        ),
        Index("ix_fdrs_institution_id_status", "institution_id", "status"),
    )

    # Ownership comes from the creating user's institution, never from request input.
    institution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("institutions.id", ondelete="RESTRICT"), nullable=False
    )
    bank_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("banks.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    fdr_number: Mapped[str] = mapped_column(String(64), nullable=False)  # as printed on the instrument
    fdr_number_normalized: Mapped[str] = mapped_column(String(64), nullable=False)
    branch_name: Mapped[str | None] = mapped_column(String(200))
    depositor_name: Mapped[str | None] = mapped_column(String(300))
    purpose: Mapped[str | None] = mapped_column(String(200))  # free text; no invented categories
    principal_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    interest_rate_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    maturity_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)  # alerts, Stage 6+
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=FdrStatus.DRAFT.value, server_default=text("'DRAFT'"), index=True
    )
    # Optimistic concurrency: the API (Stage 5c) compares and increments it under a row lock.
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FDRStatusHistory(Base):
    """Append-only record of every status change (PostgreSQL triggers reject UPDATE/DELETE/TRUNCATE).

    Reviewer comments live here rather than in the audit log because they are free text; the audit
    log keeps only allow-listed, value-free details. Tamper-evidence/defence in depth, not absolute
    immutability: a privileged database owner can still disable the triggers.
    """

    __tablename__ = "fdr_status_history"
    __table_args__ = (
        CheckConstraint(f"to_status IN ({_ALL_STATUSES})", name="to_status_valid"),
        CheckConstraint(f"from_status IS NULL OR from_status IN ({_ALL_STATUSES})", name="from_status_valid"),
        CheckConstraint("from_status IS NULL OR from_status <> to_status", name="status_changed"),
        CheckConstraint(f"actor_role IN ({_ALL_ROLES})", name="actor_role_valid"),
        CheckConstraint("comment IS NULL OR length(comment) <= 2000", name="comment_length"),
        Index("ix_fdr_status_history_fdr_id_occurred_at", "fdr_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(_HISTORY_ID, primary_key=True, autoincrement=True)
    fdr_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("fdrs.id", ondelete="RESTRICT"), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(30))  # NULL for the creation entry
    to_status: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(30), nullable=False)  # role at the time of the action
    comment: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def assert_snapshot_safe(value: Any, path: str = "snapshot") -> None:
    """Snapshots must be plain JSON where money is a decimal STRING. Floats (and Decimal objects,
    which JSON cannot carry) are rejected so precision can never be silently lost."""
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path}: keys must be strings")
            assert_snapshot_safe(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            assert_snapshot_safe(item, f"{path}[{index}]")
    elif value is None or isinstance(value, (str, bool, int)):
        return
    else:
        raise ValueError(f"{path}: {type(value).__name__} is not allowed (store money as a decimal string)")


class FDRRevision(UUIDPrimaryKeyMixin, Base):
    """Frozen copy of the submitted values, written on each submit/resubmit (append-only).

    Later stages compare document-extracted values against these snapshots, so the originally
    submitted data survives correction rounds.
    """

    __tablename__ = "fdr_revisions"
    __table_args__ = (
        UniqueConstraint("fdr_id", "revision_no", name="uq_fdr_revisions_fdr_id_revision_no"),
        CheckConstraint("revision_no >= 1", name="revision_no_positive"),
    )

    fdr_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("fdrs.id", ondelete="RESTRICT"), nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False)
    submitted_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    @validates("snapshot")
    def _validate_snapshot(self, _key: str, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("snapshot must be a JSON object")
        assert_snapshot_safe(value)
        return value


def fdr_snapshot(fdr: FDR) -> dict[str, Any]:
    """Build the JSON snapshot of an FDR's submitted values (money as exact decimal strings)."""
    principal = fdr.principal_amount
    rate = fdr.interest_rate_pct
    if isinstance(principal, float) or isinstance(rate, float):
        raise ValueError("Monetary values must be Decimal, not float")
    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "currency": ASSUMED_CURRENCY,
        "institution_id": str(fdr.institution_id),
        "bank_id": str(fdr.bank_id),
        "fdr_number": fdr.fdr_number,
        "branch_name": fdr.branch_name,
        "depositor_name": fdr.depositor_name,
        "purpose": fdr.purpose,
        "principal_amount": format(Decimal(principal), "f"),
        "interest_rate_pct": None if rate is None else format(Decimal(rate), "f"),
        "issue_date": fdr.issue_date.isoformat(),
        "maturity_date": fdr.maturity_date.isoformat(),
    }
