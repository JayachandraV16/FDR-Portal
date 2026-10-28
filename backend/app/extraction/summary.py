"""Per-FDR views of STORED extraction results: the reviewer-queue summary and the findings behind the
exceptions page.

Everything here only READS what already exists. It never starts an extraction, never reads a document,
and never changes anything. For each FDR it looks at the active documents and, per document, the latest
request and latest recorded result:

  pending   the latest request is still PENDING or RUNNING
  analyzed  the latest recorded extraction COMPLETED
  failed    the latest extraction FAILED, or the latest request FAILED with nothing newer completed
  none      no request has ever been made: the document is simply "not analyzed", which is a neutral
            state and never counted as a mismatch

Analyzed documents are compared with the FDR's latest frozen revision snapshot (what the reviewer is
reviewing), falling back to the record itself if there is none. Only an actual MISMATCH outcome counts
as a mismatch; NOT_FOUND, AMBIGUOUS and NOT_COMPARABLE are counted separately and are not discrepancies.
Nothing here says a document is genuine or fraudulent.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.extraction.analysis import fields_from_result
from app.extraction.compare import (
    AMBIGUOUS, MATCH, MISMATCH, NOT_COMPARABLE, NOT_FOUND, Submitted, compare_field, submitted_from_fdr,
    submitted_from_snapshot,
)
from app.models import (
    ACTIVE_JOB_STATUSES,
    FDR,
    Bank,
    DocumentStatus,
    FDRDocument,
    FDRDocumentExtraction,
    FDRExtractionJob,
    FDRRevision,
    JobStatus,
)
from app.schemas.extractions import DocumentCheckSummary

DOCUMENT_TABLES = ("fdr_documents", "fdr_document_extractions", "fdr_extraction_jobs")


def document_tables_ready(db: Session) -> bool:
    """False when the migrations that create the document and extraction tables have not been applied to
    this database. Callers then report 'unavailable' instead of failing (a failed statement would also
    abort the whole PostgreSQL transaction)."""
    inspector = inspect(db.get_bind())
    return all(inspector.has_table(name) for name in DOCUMENT_TABLES)


def empty_summary(status: str, total: int = 0) -> DocumentCheckSummary:
    return DocumentCheckSummary(
        status=status, documents_total=total, documents_analyzed=0, documents_pending=0, documents_failed=0,
        mismatch_count=0, ambiguous_count=0, not_found_count=0, not_comparable_count=0, match_count=0, mismatch_fields=[],
    )


@dataclass
class _Loaded:
    fdrs: list[FDR]
    documents: dict[uuid.UUID, list[uuid.UUID]] = field(default_factory=lambda: defaultdict(list))
    latest_job: dict[uuid.UUID, tuple[str, object]] = field(default_factory=dict)
    latest_extraction: dict[uuid.UUID, tuple[uuid.UUID, str, object]] = field(default_factory=dict)
    results: dict[uuid.UUID, dict] = field(default_factory=dict)
    snapshots: dict[uuid.UUID, dict] = field(default_factory=dict)
    banks: dict[uuid.UUID, Bank] = field(default_factory=dict)

    def side(self, fdr: FDR) -> Submitted:
        snapshot = self.snapshots.get(fdr.id)
        if snapshot is not None:
            bank = self.banks.get(uuid.UUID(snapshot["bank_id"])) if snapshot.get("bank_id") else None
            return submitted_from_snapshot(snapshot, bank)
        return submitted_from_fdr(fdr, self.banks.get(fdr.bank_id))

    def state(self, doc_id: uuid.UUID) -> str:
        job = self.latest_job.get(doc_id)
        extraction = self.latest_extraction.get(doc_id)
        if job is not None and job[0] in ACTIVE_JOB_STATUSES:
            return "pending"
        if extraction is not None and extraction[1] == "COMPLETED":
            return "analyzed"
        if (extraction is not None and extraction[1] == "FAILED") or (job is not None and job[0] == JobStatus.FAILED.value):
            return "failed"
        return "none"


def _load(db: Session, fdrs: list[FDR]) -> _Loaded:
    loaded = _Loaded(fdrs=fdrs)
    if not fdrs:
        return loaded
    fdr_ids = [f.id for f in fdrs]
    for fdr_id, doc_id in db.execute(
        select(FDRDocument.fdr_id, FDRDocument.id).where(
            FDRDocument.fdr_id.in_(fdr_ids), FDRDocument.status == DocumentStatus.ACTIVE.value
        )
    ):
        loaded.documents[fdr_id].append(doc_id)
    all_docs = [d for ids in loaded.documents.values() for d in ids]
    if all_docs:
        for doc_id, status, requested_at in db.execute(
            select(FDRExtractionJob.document_id, FDRExtractionJob.status, FDRExtractionJob.requested_at)
            .where(FDRExtractionJob.document_id.in_(all_docs))
            .order_by(FDRExtractionJob.requested_at.desc(), FDRExtractionJob.id.desc())
        ):
            loaded.latest_job.setdefault(doc_id, (status, requested_at))
        for ext_id, doc_id, status, requested_at in db.execute(
            select(
                FDRDocumentExtraction.id, FDRDocumentExtraction.document_id, FDRDocumentExtraction.status,
                FDRDocumentExtraction.requested_at,
            )
            .where(FDRDocumentExtraction.document_id.in_(all_docs))
            .order_by(FDRDocumentExtraction.requested_at.desc(), FDRDocumentExtraction.id.desc())
        ):
            loaded.latest_extraction.setdefault(doc_id, (ext_id, status, requested_at))
    completed_ids = [ext_id for ext_id, status, _at in loaded.latest_extraction.values() if status == "COMPLETED"]
    if completed_ids:
        loaded.results = dict(db.execute(
            select(FDRDocumentExtraction.id, FDRDocumentExtraction.result).where(FDRDocumentExtraction.id.in_(completed_ids))
        ).all())
    for fdr_id, snapshot in db.execute(
        select(FDRRevision.fdr_id, FDRRevision.snapshot).where(FDRRevision.fdr_id.in_(fdr_ids))
        .order_by(FDRRevision.revision_no.desc())
    ):
        loaded.snapshots.setdefault(fdr_id, snapshot)
    bank_ids = {uuid.UUID(s["bank_id"]) for s in loaded.snapshots.values() if s.get("bank_id")} | {f.bank_id for f in fdrs}
    if bank_ids:
        loaded.banks = {b.id: b for b in db.scalars(select(Bank).where(Bank.id.in_(bank_ids)))}
    return loaded


def _outcomes(loaded: _Loaded, fdr: FDR, doc_id: uuid.UUID) -> list[tuple[str, str]]:
    """(field name, outcome) for one analyzed document, against the FDR's submitted version."""
    extraction = loaded.latest_extraction[doc_id]
    side = loaded.side(fdr)
    return [(raw.name, compare_field(raw, side).outcome) for raw in fields_from_result(loaded.results.get(extraction[0]) or {})]


def document_checks(db: Session, fdrs: list[FDR]) -> dict[uuid.UUID, DocumentCheckSummary]:
    """A summary for each of `fdrs` (all of which the caller has already been authorised to see)."""
    loaded = _load(db, fdrs)
    out: dict[uuid.UUID, DocumentCheckSummary] = {}
    for fdr in fdrs:
        doc_ids = loaded.documents.get(fdr.id, [])
        if not doc_ids:
            out[fdr.id] = empty_summary("NO_DOCUMENTS")
            continue
        pending = analyzed = failed = 0
        counts = {MATCH: 0, MISMATCH: 0, NOT_FOUND: 0, AMBIGUOUS: 0, NOT_COMPARABLE: 0}
        mismatching: list[str] = []
        for doc_id in doc_ids:
            state = loaded.state(doc_id)
            if state == "pending":
                pending += 1
            elif state == "analyzed":
                analyzed += 1
                for name, outcome in _outcomes(loaded, fdr, doc_id):
                    counts[outcome] += 1
                    if outcome == MISMATCH and name not in mismatching:
                        mismatching.append(name)
            elif state == "failed":
                failed += 1
        status = "PENDING" if pending else "ANALYZED" if analyzed else "FAILED" if failed else "NOT_ANALYZED"
        out[fdr.id] = DocumentCheckSummary(
            status=status, documents_total=len(doc_ids), documents_analyzed=analyzed, documents_pending=pending,
            documents_failed=failed, mismatch_count=counts[MISMATCH], ambiguous_count=counts[AMBIGUOUS],
            not_found_count=counts[NOT_FOUND], not_comparable_count=counts[NOT_COMPARABLE], match_count=counts[MATCH],
            mismatch_fields=mismatching,
        )
    return out


@dataclass(frozen=True)
class FdrFindings:
    """What the stored results say about one FDR, for the exceptions page."""

    documents_total: int
    failed_documents: int
    mismatch_fields: tuple[str, ...]
    ambiguous_fields: tuple[str, ...]


def document_findings(db: Session, fdrs: list[FDR]) -> dict[uuid.UUID, FdrFindings]:
    loaded = _load(db, fdrs)
    out: dict[uuid.UUID, FdrFindings] = {}
    for fdr in fdrs:
        doc_ids = loaded.documents.get(fdr.id, [])
        failed = 0
        mismatch: list[str] = []
        ambiguous: list[str] = []
        for doc_id in doc_ids:
            state = loaded.state(doc_id)
            if state == "failed":
                failed += 1
            elif state == "analyzed":
                for name, outcome in _outcomes(loaded, fdr, doc_id):
                    if outcome == MISMATCH and name not in mismatch:
                        mismatch.append(name)
                    elif outcome == AMBIGUOUS and name not in ambiguous:
                        ambiguous.append(name)
        out[fdr.id] = FdrFindings(len(doc_ids), failed, tuple(mismatch), tuple(ambiguous))
    return out
