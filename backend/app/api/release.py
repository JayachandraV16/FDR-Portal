"""Release / refund REQUESTS and their decisions. This is an authorisation workflow, not a payment.

  GET  /release                       FDRs eligible for release (accepted and matured), with their state
  GET  /fdrs/{id}/release             one FDR's eligibility, state and history
  POST /fdrs/{id}/release/request     INSTITUTION_USER (own institution): ask for release
  POST /fdrs/{id}/release/approve     AICTE_APPROVER: authorise the release
  POST /fdrs/{id}/release/reject      AICTE_APPROVER: decline it, with a fixed reason code

Rules: only an ACCEPTED FDR that has reached its maturity date is eligible. A request is possible when
there is no open request and nothing has been approved (a rejected request may be made again). Only an
approver may decide, and only on an open request. Nothing here changes the FDR or moves money: an
"approved" release is an authorisation recorded in the audit trail by a named approver, and the actual
transfer is outside this system. There is no release table (it would need a migration that has not been
approved), so state is read from those audit events (see app/insights/release_state.py).

Each action takes the FDR row lock first (the same order as every other write), re-checks eligibility and
state under it, and writes its audit event in the same transaction, so two simultaneous requests or
decisions cannot both succeed.
"""

import math
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, RequestContext, deny, get_request_context, require_authenticated, require_roles
from app.api.fdrs import locked_visible_fdr
from app.audit import AuditOutcome, write_audit_event
from app.db import get_db
from app.insights import release_state as rs
from app.insights.derive import today
from app.models import FDR, Bank, FdrStatus, Institution, Role, User
from app.schemas.insights import ReleaseEvent, ReleaseList, ReleaseOut
from app.security.scope import fdr_visibility

router = APIRouter(tags=["release"])
institution_only = require_roles(Role.INSTITUTION_USER)
approvers = require_roles(Role.AICTE_APPROVER)

NOTE = ("A release request is an authorisation workflow recorded in the audit trail. Approval is not a payment: "
        "this system does not transfer funds.")
LIST_SCAN = 5000


class RejectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Literal["INCOMPLETE_SUPPORTING_INFORMATION", "NOT_YET_DUE", "DETAILS_NEED_CORRECTION", "OTHER"]


def _out(user: User, fdr: FDR, inst: Institution, bank: Bank, state: str, today_, history=None) -> ReleaseOut:
    eligible = rs.is_eligible(fdr, today_)
    return ReleaseOut(
        fdr_id=fdr.id, fdr_number=fdr.fdr_number, institution_code=inst.code, institution_name=inst.name, bank_name=bank.name,
        principal_amount=format(fdr.principal_amount, "f"), maturity_date=fdr.maturity_date, fdr_status=fdr.status,
        matured=fdr.maturity_date <= today_, eligible=eligible, eligibility_note=rs.eligibility_note(fdr, today_), state=state,
        can_request=eligible and user.role == Role.INSTITUTION_USER.value and state in (rs.NONE, rs.REJECTED),
        can_decide=eligible and user.role == Role.AICTE_APPROVER.value and state == rs.REQUESTED,
        history=history or [],
    )


def _history(db: Session, fdr_id: uuid.UUID) -> list[ReleaseEvent]:
    return [
        ReleaseEvent(action=e.action, occurred_at=e.occurred_at, actor_role=e.actor_role, actor_name=name,
                     reason=(e.details or {}).get("reason"))
        for e, name in rs.release_history(db, fdr_id)
    ]


def _load(db: Session, user: User, fdr_id: uuid.UUID):
    row = db.execute(
        select(FDR, Institution, Bank).join(Institution, Institution.id == FDR.institution_id).join(Bank, Bank.id == FDR.bank_id)
        .where(FDR.id == fdr_id, fdr_visibility(user))
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="FDR not found")
    return row


@router.get("/release", response_model=ReleaseList)
def list_release(
    response: Response,
    state: Literal["NONE", "REQUESTED", "APPROVED", "REJECTED"] | None = Query(None),
    page: int = Query(1, ge=1, le=100_000),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_authenticated),
) -> ReleaseList:
    """Accepted FDRs that have matured, with their release state, oldest maturity first. The history of
    each is not included here (use GET /fdrs/{id}/release)."""
    response.headers["Cache-Control"] = "no-store"
    now = today()
    rows = db.execute(
        select(FDR, Institution, Bank).join(Institution, Institution.id == FDR.institution_id).join(Bank, Bank.id == FDR.bank_id)
        .where(fdr_visibility(auth.user), FDR.status == FdrStatus.ACCEPTED.value, FDR.maturity_date <= now)
        .order_by(FDR.maturity_date, FDR.id).limit(LIST_SCAN)
    ).all()
    states = rs.release_states(db, [f.id for f, _i, _b in rows])
    if state is not None:
        rows = [r for r in rows if states[r[0].id] == state]
    total = len(rows)
    window = rows[(page - 1) * page_size : page * page_size]
    return ReleaseList(
        items=[_out(auth.user, f, i, b, states[f.id], now) for f, i, b in window], page=page, page_size=page_size,
        total=total, total_pages=math.ceil(total / page_size), note=NOTE,
    )


@router.get("/fdrs/{fdr_id}/release", response_model=ReleaseOut)
def get_release(
    fdr_id: uuid.UUID, response: Response, db: Session = Depends(get_db), auth: AuthContext = Depends(require_authenticated),
) -> ReleaseOut:
    response.headers["Cache-Control"] = "no-store"
    fdr, inst, bank = _load(db, auth.user, fdr_id)
    state = rs.release_states(db, [fdr.id])[fdr.id]
    return _out(auth.user, fdr, inst, bank, state, today(), _history(db, fdr.id))


def _act(db, request, ctx, auth, fdr_id, *, action: str, allowed_states: tuple[str, ...], reason: str | None) -> ReleaseOut:
    fdr = locked_visible_fdr(db, request, auth, ctx, fdr_id)  # row lock + visibility (404 otherwise, audited)
    now = today()

    def refuse(message: str, why: str):
        return deny(db, request, auth, ctx, action=action, status_code=409, message=message, reason=why, target_fdr_id=str(fdr.id))

    if not rs.is_eligible(fdr, now):
        raise refuse(rs.eligibility_note(fdr, now), "not_eligible")
    state = rs.release_states(db, [fdr.id])[fdr.id]
    if state not in allowed_states:
        raise refuse({
            "release.requested": "A release request is already open, or the release has been approved",
            "release.approved": "There is no open release request to decide",
            "release.rejected": "There is no open release request to decide",
        }[action], "invalid_release_state")
    details = {"target_fdr_id": str(fdr.id)}
    if reason:
        details["reason"] = reason
    write_audit_event(
        db, action=action, outcome=AuditOutcome.SUCCESS, actor_user_id=auth.user.id, actor_role=auth.user.role,
        entity_type="fdr", entity_id=fdr.id, ip_address=ctx.ip_address, request_id=ctx.request_id, details=details,
    )
    db.commit()
    row = _load(db, auth.user, fdr_id)
    new_state = rs.release_states(db, [fdr_id])[fdr_id]
    return _out(auth.user, *row, new_state, now, _history(db, fdr_id))


@router.post("/fdrs/{fdr_id}/release/request", response_model=ReleaseOut, status_code=201)
def request_release(
    fdr_id: uuid.UUID, request: Request, response: Response, db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context), auth: AuthContext = Depends(institution_only),
) -> ReleaseOut:
    response.headers["Cache-Control"] = "no-store"
    return _act(db, request, ctx, auth, fdr_id, action="release.requested", allowed_states=(rs.NONE, rs.REJECTED), reason=None)


@router.post("/fdrs/{fdr_id}/release/approve", response_model=ReleaseOut)
def approve_release(
    fdr_id: uuid.UUID, request: Request, response: Response, db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context), auth: AuthContext = Depends(approvers),
) -> ReleaseOut:
    response.headers["Cache-Control"] = "no-store"
    return _act(db, request, ctx, auth, fdr_id, action="release.approved", allowed_states=(rs.REQUESTED,), reason=None)


@router.post("/fdrs/{fdr_id}/release/reject", response_model=ReleaseOut)
def reject_release(
    fdr_id: uuid.UUID, body: RejectBody, request: Request, response: Response, db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context), auth: AuthContext = Depends(approvers),
) -> ReleaseOut:
    response.headers["Cache-Control"] = "no-store"
    return _act(db, request, ctx, auth, fdr_id, action="release.rejected", allowed_states=(rs.REQUESTED,), reason=body.reason)
