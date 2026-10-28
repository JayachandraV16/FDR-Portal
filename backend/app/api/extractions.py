"""Automatic extraction of fields from an uploaded document, and comparison with the submitted FDR.

Everything here is assistance for a human reviewer. It never changes an FDR, never touches a status,
and never decides anything: the review decisions stay in app/api/review.py. Extracted values live in
their own table and are never written to the FDR's columns. A result says what text was found in a
file, not that the file is genuine or that the bank issued it.

Permissions (server-side; session, Origin, CSRF and the password-change gate come from require_roles):
  * request an extraction (and retry one), read the latest result, read the comparison: AICTE_REVIEWER
    and AICTE_APPROVER, for documents of records they may review (the same visibility as the FDR and
    its documents, so DRAFTs are unreachable). INSTITUTION_USER, ADMIN and AUDITOR are refused (403):
    the comparison exposes review-side material, and no policy has granted it to them.
  * a record outside the caller's visibility is a 404 identical to a missing one.
  * authorization is checked when a request is accepted and again on every read of a result; the
    worker that later runs the job acts on an already-authorised request and exposes nothing itself.

Requests are asynchronous (see app/extraction/jobs.py). POST only validates, enqueues a job and returns
202 with the job; a worker runs it. Asking again while a job for that document is still PENDING or
RUNNING returns that same job with 200 (so a double click or a retry cannot queue duplicates), and a
full queue answers 503. A FAILED job can be retried by simply asking again. The audit trail records
three distinct events: `document.extract_requested`, `document.extract_completed` and
`document.extract_failed`, naming only ids, counts and fixed reason codes, never text or values.

The comparison is computed on every read, from the stored result, against (a) the FDR as it is now and
(b) the frozen revision snapshot of the submission, so later edits cannot rewrite what was submitted.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, RequestContext, deny, get_request_context, require_roles
from app.api.documents import _visible_document
from app.audit import AuditOutcome, write_audit_event
from app.config import Settings, get_settings
from app.db import get_db
from app.extraction.analysis import ERROR_MESSAGES, fields_from_result
from app.extraction.compare import FIELD_LABELS, compare_field, submitted_from_fdr, submitted_from_snapshot
from app.extraction.jobs import QueueFull, enqueue
from app.extraction.types import Limits
from app.models import FDR, Bank, FDRDocumentExtraction, FDRExtractionJob, FDRRevision, Role
from app.schemas.extractions import (
    CandidateOut,
    ComparisonOut,
    ExtractedFieldOut,
    ExtractionOut,
    FieldComparisonOut,
    JobOut,
    SideOut,
)

router = APIRouter(tags=["extraction"])
reviewers = require_roles(Role.AICTE_REVIEWER, Role.AICTE_APPROVER)


def limits_from(settings: Settings) -> Limits:
    return Limits(
        max_pages=settings.extraction_max_pages, max_text_chars=settings.extraction_max_text_chars,
        timeout_seconds=settings.extraction_timeout_seconds, max_image_pixels=settings.extraction_max_image_pixels,
    )


def _field_out(raw) -> ExtractedFieldOut:
    return ExtractedFieldOut(
        name=raw.name, label=FIELD_LABELS.get(raw.name, raw.name), status=raw.status, value=raw.value,
        currency=raw.currency, confidence=raw.confidence, page=raw.page, source=raw.source,
        candidates=[CandidateOut(**c) for c in raw.candidates], note=raw.note,
        raw=raw.raw if raw.raw not in (None, raw.value) else None,
    )


def _extraction_out(row: FDRDocumentExtraction) -> ExtractionOut:
    result = row.result or {}
    return ExtractionOut(
        id=row.id, document_id=row.document_id, status=row.status, error_code=row.error_code,
        error_message=ERROR_MESSAGES.get(row.error_code) if row.error_code else None,
        extractor=row.extractor, requested_at=row.requested_at,
        pages_total=int(result.get("pages_total", 0)), pages_processed=int(result.get("pages_processed", 0)),
        ocr_pages=int(result.get("ocr_pages", 0)), truncated=bool(result.get("truncated", False)),
        warnings=list(result.get("warnings", [])), fields=[_field_out(f) for f in fields_from_result(result)],
    )


def _job_out(job: FDRExtractionJob) -> JobOut:
    return JobOut(
        id=job.id, document_id=job.document_id, status=job.status, requested_at=job.requested_at,
        started_at=job.started_at, finished_at=job.finished_at, attempts=job.attempts, error_code=job.error_code,
        error_message=ERROR_MESSAGES.get(job.error_code) if job.error_code else None,
    )


def _latest(db: Session, document_id: uuid.UUID) -> FDRDocumentExtraction | None:
    return db.scalars(
        select(FDRDocumentExtraction)
        .where(FDRDocumentExtraction.document_id == document_id)
        .order_by(FDRDocumentExtraction.requested_at.desc(), FDRDocumentExtraction.id.desc())
        .limit(1)
    ).first()


def _latest_job(db: Session, document_id: uuid.UUID) -> FDRExtractionJob | None:
    return db.scalars(
        select(FDRExtractionJob)
        .where(FDRExtractionJob.document_id == document_id)
        .order_by(FDRExtractionJob.requested_at.desc(), FDRExtractionJob.id.desc())
        .limit(1)
    ).first()


@router.post("/fdrs/{fdr_id}/documents/{document_id}/extractions", response_model=JobOut, status_code=202)
def start_extraction(
    fdr_id: uuid.UUID,
    document_id: uuid.UUID,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(reviewers),
    settings: Settings = Depends(get_settings),
) -> JobOut:
    """Accept an extraction request: 202 with a new PENDING job, 200 with the existing job if one is
    already pending or running for this document, 503 if the queue is full. Used for retries too."""
    response.headers["Cache-Control"] = "no-store"
    _visible_document(db, request, auth, ctx, fdr_id, document_id)  # authorization, before anything is accepted
    try:
        job, created = enqueue(
            db, document_id=document_id, fdr_id=fdr_id, user_id=auth.user.id, queue_limit=settings.extraction_queue_limit
        )
    except QueueFull:
        raise deny(
            db, request, auth, ctx,
            action="document.extract_requested", status_code=503,
            message="The extraction queue is full. Try again in a moment.",
            reason="queue_full", target_fdr_id=str(fdr_id), target_document_id=str(document_id),
        ) from None
    if not created:
        response.status_code = 200  # already queued or running: nothing new was accepted
        return _job_out(job)
    write_audit_event(
        db, action="document.extract_requested", outcome=AuditOutcome.SUCCESS,
        actor_user_id=auth.user.id, actor_role=auth.user.role, entity_type="extraction_job", entity_id=job.id,
        ip_address=ctx.ip_address, request_id=ctx.request_id,
        details={"target_fdr_id": str(fdr_id), "target_document_id": str(document_id)},
    )
    db.commit()
    runner = getattr(request.app.state, "extraction_runner", None)
    if runner is not None:
        runner.wake()
    return _job_out(job)


@router.get("/fdrs/{fdr_id}/documents/{document_id}/extraction", response_model=ExtractionOut)
def latest_extraction(
    fdr_id: uuid.UUID,
    document_id: uuid.UUID,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(reviewers),
) -> ExtractionOut:
    response.headers["Cache-Control"] = "no-store"
    _visible_document(db, request, auth, ctx, fdr_id, document_id)
    row = _latest(db, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No extraction has been run for this document")
    return _extraction_out(row)


@router.get("/fdrs/{fdr_id}/documents/{document_id}/comparison", response_model=ComparisonOut)
def comparison(
    fdr_id: uuid.UUID,
    document_id: uuid.UUID,
    request: Request,
    response: Response,
    revision_no: int | None = Query(None, ge=1, description="Compare with this submission; default is the latest"),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(reviewers),
) -> ComparisonOut:
    """Field-by-field comparison of the latest extraction with the FDR as it is now and with the frozen
    snapshot of a submission, plus the state of the latest request (`job`). With no extraction yet,
    `extraction` is null and `fields` is empty."""
    response.headers["Cache-Control"] = "no-store"
    _visible_document(db, request, auth, ctx, fdr_id, document_id)
    row = _latest(db, document_id)
    job = _latest_job(db, document_id)
    revision = None
    if revision_no is None:
        revision = db.scalars(
            select(FDRRevision).where(FDRRevision.fdr_id == fdr_id).order_by(FDRRevision.revision_no.desc()).limit(1)
        ).first()
    else:
        revision = db.scalars(
            select(FDRRevision).where(FDRRevision.fdr_id == fdr_id, FDRRevision.revision_no == revision_no)
        ).first()
        if revision is None:
            raise HTTPException(status_code=404, detail="Revision not found")

    base = ComparisonOut(
        document_id=document_id, extraction=None,
        revision_no=revision.revision_no if revision else None,
        revision_submitted_at=revision.submitted_at if revision else None, fields=[],
        job=_job_out(job) if job is not None else None,
    )
    if row is None:
        return base
    base.extraction = _extraction_out(row)
    if row.status != "COMPLETED":
        return base

    fdr = db.get(FDR, fdr_id)
    current = submitted_from_fdr(fdr, db.get(Bank, fdr.bank_id))
    frozen = None
    if revision is not None:
        snapshot = revision.snapshot or {}
        bank_id = snapshot.get("bank_id")
        bank = db.get(Bank, uuid.UUID(bank_id)) if bank_id else None
        frozen = submitted_from_snapshot(snapshot, bank)
    for raw in fields_from_result(row.result):
        now = compare_field(raw, current)
        then = compare_field(raw, frozen) if frozen is not None else None
        base.fields.append(FieldComparisonOut(
            field=raw.name, label=FIELD_LABELS.get(raw.name, raw.name), extracted=_field_out(raw),
            current=SideOut(submitted_value=now.submitted_value, outcome=now.outcome, explanation=now.explanation),
            revision=None if then is None else SideOut(
                submitted_value=then.submitted_value, outcome=then.outcome, explanation=then.explanation
            ),
        ))
    return base
