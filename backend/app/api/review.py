"""AICTE review workflow: the reviewer queue and the review decisions.

State machine (server-side; no endpoint accepts a status from the client):

    SUBMITTED --request-correction--> CORRECTION_REQUESTED   AICTE_REVIEWER or AICTE_APPROVER; comment required
    SUBMITTED --accept--------------> ACCEPTED               AICTE_APPROVER only;               comment optional
    SUBMITTED --reject--------------> REJECTED               AICTE_APPROVER only;               comment required
    CORRECTION_REQUESTED --(institution resubmits, see fdrs.py)--> SUBMITTED

ACCEPTED, REJECTED and WITHDRAWN are terminal. Only a SUBMITTED record can be decided, so a repeated,
stale or concurrent decision gets 409 and cannot flip an outcome that already happened.

Separation of duties. Reviewers screen records and send them back; the final accept/reject belongs
to approvers. There is deliberately no "recommended" status (the status model has none), so a
reviewer cannot record a recommendation. ADMIN has no review rights merely for being an
administrator, and AUDITOR is read-only; both are refused with 403 (audited).

"ACCEPTED" means accepted after human review of what the institution submitted. It is not bank
verification, and nothing here implies that it is.

Every decision runs in one transaction: lock the row (found through the caller's visibility scope),
re-check status and row_version, change the record, append the status-history row (which carries the
actor, role, comment and time), and write the audit event. The audit event names the field that
changed but never the comment: free text lives only in the history table.
"""

import logging
import math
import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, RequestContext, deny, get_request_context, require_roles
from app.api.fdrs import (
    _audit,
    _joined,
    _response,
    _to_out,
    locked_visible_fdr,
    require_status_and_version,
    scoped_conditions,
)
from app.db import get_db
from app.extraction.summary import document_checks, document_tables_ready, empty_summary
from app.models import FDR, FdrStatus, FDRStatusHistory, Role
from app.schemas.extractions import ReviewQueueItem, ReviewQueueList
from app.schemas.fdrs import AcceptRequest, CorrectionRequest, FdrOut, RejectRequest
from app.timeutil import utcnow

router = APIRouter(tags=["review"])
log = logging.getLogger("fdr.review")

reviewers = require_roles(Role.AICTE_REVIEWER, Role.AICTE_APPROVER)
approvers = require_roles(Role.AICTE_APPROVER)

MAX_PAGE = 100_000
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25


@router.get("/review/queue", response_model=ReviewQueueList)
def review_queue(
    response: Response,
    page: int = Query(1, ge=1, le=MAX_PAGE),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    bank_id: uuid.UUID | None = Query(None),
    institution_id: uuid.UUID | None = Query(None),
    q: str | None = Query(None, min_length=1, max_length=64, description="FDR number prefix"),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(reviewers),
) -> ReviewQueueList:
    """FDRs awaiting a decision (status SUBMITTED), oldest submission first. Scoped by the same
    visibility rule as the registry, so drafts are never included. Each entry carries a summary of the
    STORED document-check results (never started from here): see app/extraction/summary.py."""
    response.headers["Cache-Control"] = "no-store"
    conditions = scoped_conditions(auth.user, bank_id=bank_id, institution_id=institution_id, q=q)
    conditions.append(FDR.status == FdrStatus.SUBMITTED.value)
    total = db.scalar(select(func.count()).select_from(FDR).where(*conditions)) or 0
    rows = db.execute(
        _joined()
        .where(*conditions)
        # First in, first out; the id breaks ties so pages never overlap or skip.
        .order_by(FDR.submitted_at.asc(), FDR.id.asc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()
    try:
        # Before the document/extraction migrations are applied there is nothing to summarise (and a failed
        # statement would abort the PostgreSQL transaction), so the summary is reported as unavailable.
        checks = document_checks(db, [f for f, _b, _i in rows]) if document_tables_ready(db) else {}
    except Exception:  # the queue must stay usable; the summary is reported as unavailable, not guessed
        log.warning("document-check summaries could not be computed for the review queue (details suppressed)")
        checks = {}
    return ReviewQueueList(
        items=[
            ReviewQueueItem(**_to_out(f, b, i).model_dump(), document_check=checks.get(f.id) or empty_summary("UNAVAILABLE"))
            for f, b, i in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=math.ceil(total / page_size),
    )


def _decide(
    db: Session, request: Request, ctx: RequestContext, auth: AuthContext, fdr_id: uuid.UUID,
    *, action: str, target: FdrStatus, row_version: int, comment: str | None,
) -> FdrOut:
    fdr = locked_visible_fdr(db, request, auth, ctx, fdr_id)
    require_status_and_version(
        db, request, auth, ctx, fdr,
        action=action, row_version=row_version, allowed=(FdrStatus.SUBMITTED,),
        wrong_status_message="This FDR is not awaiting review",
    )
    previous = fdr.status
    fdr.status = target.value
    fdr.row_version += 1
    if target in (FdrStatus.ACCEPTED, FdrStatus.REJECTED):
        fdr.decided_at = utcnow()
    try:
        db.add(FDRStatusHistory(
            fdr_id=fdr.id, from_status=previous, to_status=target.value,
            actor_user_id=auth.user.id, actor_role=auth.user.role, comment=comment,
        ))
        _audit(db, ctx, auth, action, fdr, changed_fields=["status"])
        db.commit()
    except IntegrityError:  # a constraint we did not anticipate: fail closed, change nothing
        db.rollback()
        raise deny(
            db, request, auth, ctx,
            action=action, status_code=409, message="The decision conflicts with existing data",
            reason="integrity_error", target_fdr_id=str(fdr_id),
        ) from None
    return _response(db, fdr.id)


@router.post("/fdrs/{fdr_id}/request-correction", response_model=FdrOut)
def request_correction(
    fdr_id: uuid.UUID,
    body: CorrectionRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(reviewers),
) -> FdrOut:
    response.headers["Cache-Control"] = "no-store"
    return _decide(
        db, request, ctx, auth, fdr_id,
        action="fdr.request_correction", target=FdrStatus.CORRECTION_REQUESTED,
        row_version=body.row_version, comment=body.comment,
    )


@router.post("/fdrs/{fdr_id}/accept", response_model=FdrOut)
def accept_fdr(
    fdr_id: uuid.UUID,
    body: AcceptRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(approvers),
) -> FdrOut:
    response.headers["Cache-Control"] = "no-store"
    return _decide(
        db, request, ctx, auth, fdr_id,
        action="fdr.accept", target=FdrStatus.ACCEPTED, row_version=body.row_version, comment=body.comment,
    )


@router.post("/fdrs/{fdr_id}/reject", response_model=FdrOut)
def reject_fdr(
    fdr_id: uuid.UUID,
    body: RejectRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(approvers),
) -> FdrOut:
    response.headers["Cache-Control"] = "no-store"
    return _decide(
        db, request, ctx, auth, fdr_id,
        action="fdr.reject", target=FdrStatus.REJECTED, row_version=body.row_version, comment=body.comment,
    )
