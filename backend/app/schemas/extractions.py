import uuid
from datetime import datetime

from pydantic import BaseModel

from app.schemas.fdrs import FdrOut


class CandidateOut(BaseModel):
    value: str
    page: int


class ExtractedFieldOut(BaseModel):
    name: str
    label: str
    status: str  # EXTRACTED | AMBIGUOUS | NOT_FOUND
    value: str | None
    currency: str | None
    confidence: str | None  # HIGH | MEDIUM | LOW, see app.extraction.fields
    page: int | None
    source: str | None  # EMBEDDED_TEXT | OCR
    candidates: list[CandidateOut]
    note: str | None
    raw: str | None  # the value exactly as printed (for example Devanagari digits), when it differs from `value`


class ExtractionOut(BaseModel):
    """A run of automatic extraction. Assistance for human review: not verification of the document."""

    id: uuid.UUID
    document_id: uuid.UUID
    status: str  # COMPLETED | FAILED
    error_code: str | None
    error_message: str | None
    extractor: str
    requested_at: datetime
    pages_total: int
    pages_processed: int
    ocr_pages: int
    truncated: bool
    warnings: list[str]
    fields: list[ExtractedFieldOut]


class JobOut(BaseModel):
    """An extraction request moving through the background queue."""

    id: uuid.UUID
    document_id: uuid.UUID
    status: str  # PENDING | RUNNING | COMPLETED | FAILED
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    attempts: int
    error_code: str | None
    error_message: str | None  # fixed, sanitized wording; never document text


class SideOut(BaseModel):
    submitted_value: str | None
    outcome: str  # MATCH | MISMATCH | NOT_FOUND | AMBIGUOUS | NOT_COMPARABLE
    explanation: str


class FieldComparisonOut(BaseModel):
    field: str
    label: str
    extracted: ExtractedFieldOut
    current: SideOut  # against the FDR record as it is now
    revision: SideOut | None  # against the frozen snapshot of the submission, if there is one


class DocumentCheckSummary(BaseModel):
    """What the stored extraction results say about an FDR's documents, for the review queue. Computed
    on the server from results that already exist; it never starts an extraction and says nothing about
    whether a document is genuine. `NOT_ANALYZED` is neutral: absence of a result is not a mismatch."""

    status: str  # NO_DOCUMENTS | NOT_ANALYZED | PENDING | FAILED | ANALYZED
    documents_total: int
    documents_analyzed: int
    documents_pending: int
    documents_failed: int
    mismatch_count: int  # field outcomes that are an actual MISMATCH with the submitted version
    ambiguous_count: int
    not_found_count: int
    not_comparable_count: int
    match_count: int
    mismatch_fields: list[str]  # which fields mismatch (names), for a tooltip


class ComparisonOut(BaseModel):
    document_id: uuid.UUID
    extraction: ExtractionOut | None
    revision_no: int | None
    revision_submitted_at: datetime | None
    fields: list[FieldComparisonOut]
    job: JobOut | None  # the latest request for this document; PENDING/RUNNING means a check is in progress


class ReviewQueueItem(FdrOut):
    """A queue entry: the FDR exactly as the registry shows it, plus the document-check summary."""

    document_check: DocumentCheckSummary


class ReviewQueueList(BaseModel):
    items: list[ReviewQueueItem]
    page: int
    page_size: int
    total: int
    total_pages: int
