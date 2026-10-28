"""Metadata for documents attached to an FDR. The bytes live in private file storage, never here.

A stored document is something an institution uploaded, in a format whose signature was checked. It
is not evidence that the FDR is genuine or that a bank issued it: nothing in this table implies
verification.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.models.fdr import _JSON


class DocumentCategory(enum.StrEnum):
    FDR_CERTIFICATE = "FDR_CERTIFICATE"  # the FDR certificate / bank receipt
    SECURITY_DEPOSIT_PROOF = "SECURITY_DEPOSIT_PROOF"
    SUPPORTING_LETTER = "SUPPORTING_LETTER"
    OTHER = "OTHER"


class DocumentStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    DELETED = "DELETED"  # hidden from every listing; the row is kept as a record


# The only content types the API accepts. Keep in step with app.documents.validation.
ALLOWED_CONTENT_TYPES = ("application/pdf", "image/jpeg", "image/png")


def _in_list(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


class FDRDocument(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "fdr_documents"
    __table_args__ = (
        CheckConstraint(f"category IN ({_in_list(c.value for c in DocumentCategory)})", name="category_valid"),
        CheckConstraint(f"status IN ({_in_list(s.value for s in DocumentStatus)})", name="status_valid"),
        CheckConstraint(f"content_type IN ({_in_list(ALLOWED_CONTENT_TYPES)})", name="content_type_valid"),
        CheckConstraint("size_bytes > 0", name="size_positive"),
        CheckConstraint("length(sha256) = 64", name="sha256_length"),
        CheckConstraint("length(storage_key) = 32", name="storage_key_length"),
        CheckConstraint("length(original_filename) > 0", name="original_filename_nonempty"),
        CheckConstraint(
            "(status = 'DELETED') = (deleted_at IS NOT NULL)", name="deleted_at_matches_status"
        ),
        Index("ix_fdr_documents_fdr_id_status", "fdr_id", "status"),
    )

    fdr_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("fdrs.id", ondelete="RESTRICT"), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    # Server-generated random identifier of the stored file; never derived from user input and never
    # returned by the API.
    storage_key: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    # Sanitised for display only. It is never used to build a filesystem path.
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)  # from the file signature
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DocumentStatus.ACTIVE.value, server_default=text("'ACTIVE'")
    )
    uploaded_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExtractionStatus(enum.StrEnum):
    COMPLETED = "COMPLETED"  # text was obtained and every field was attempted (some may be NOT_FOUND)
    FAILED = "FAILED"  # no usable text; error_code says why


class FDRDocumentExtraction(UUIDPrimaryKeyMixin, Base):
    """One run of automatic extraction over one stored document.

    This is assistance for a human reviewer, kept apart from the authoritative FDR columns: nothing
    here is ever written back to `fdrs`. `result` holds the field values the extractor found (and
    their confidence and page), never the document's text. Runs are not edited afterwards; a retry
    adds a new row and the latest one is shown.
    """

    __tablename__ = "fdr_document_extractions"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in_list(s.value for s in ExtractionStatus)})", name="status_valid"),
        CheckConstraint("(status = 'FAILED') = (error_code IS NOT NULL)", name="error_code_matches_status"),
        Index("ix_fdr_document_extractions_document_id_requested_at", "document_id", "requested_at"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("fdr_documents.id", ondelete="RESTRICT"), nullable=False)
    fdr_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("fdrs.id", ondelete="RESTRICT"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(40))
    extractor: Mapped[str] = mapped_column(String(120), nullable=False)  # components used, e.g. "pypdf 6.21.2"
    result: Mapped[dict] = mapped_column(_JSON, nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class JobStatus(enum.StrEnum):
    PENDING = "PENDING"  # accepted and waiting for a worker
    RUNNING = "RUNNING"  # claimed by a worker, which holds a lease
    COMPLETED = "COMPLETED"  # a COMPLETED extraction was recorded
    FAILED = "FAILED"  # no usable result: the document could not be analysed, or the job was interrupted


ACTIVE_JOB_STATUSES = (JobStatus.PENDING.value, JobStatus.RUNNING.value)


class FDRExtractionJob(UUIDPrimaryKeyMixin, Base):
    """One requested extraction run, from acceptance to outcome.

    The table is the queue: a worker claims a PENDING row with an atomic conditional UPDATE, so two
    workers can never take the same job, and a RUNNING row whose lease has expired (the process died)
    is put back or failed by a recovery sweep. The result itself is a row in fdr_document_extractions
    (`extraction_id`); this row only tracks the lifecycle. At most one job per document can be
    PENDING or RUNNING (a partial unique index), which also makes a repeated request idempotent.
    """

    __tablename__ = "fdr_extraction_jobs"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in_list(s.value for s in JobStatus)})", name="status_valid"),
        CheckConstraint("(status = 'FAILED') = (error_code IS NOT NULL)", name="error_code_matches_status"),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        Index(
            "uq_fdr_extraction_jobs_one_active_per_document", "document_id", unique=True,
            postgresql_where=text("status IN ('PENDING', 'RUNNING')"),
            sqlite_where=text("status IN ('PENDING', 'RUNNING')"),
        ),
        Index("ix_fdr_extraction_jobs_status_requested_at", "status", "requested_at"),
        Index("ix_fdr_extraction_jobs_document_id_requested_at", "document_id", "requested_at"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("fdr_documents.id", ondelete="RESTRICT"), nullable=False)
    fdr_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("fdrs.id", ondelete="RESTRICT"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=JobStatus.PENDING.value, server_default=text("'PENDING'"))
    requested_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_by: Mapped[str | None] = mapped_column(String(64))  # the worker that holds the lease
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    error_code: Mapped[str | None] = mapped_column(String(40))
    extraction_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("fdr_document_extractions.id", ondelete="RESTRICT"))
