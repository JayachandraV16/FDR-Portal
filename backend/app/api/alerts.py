"""Computed alerts and exceptions. Read-only; nothing is stored (see app/insights/derive.py).

  GET /alerts       every signed-in role, restricted to the FDRs that role may see
  GET /exceptions   every signed-in role, but extraction-derived exceptions only for reviewers/approvers
                    and document-related ones only for roles that may use documents
"""

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, require_authenticated
from app.db import get_db
from app.insights.derive import build_alerts, build_exceptions
from app.models import FDR, FDRStatusHistory, Institution
from app.schemas.insights import ActivityItem, ActivityOut, AlertsOut, ExceptionsOut
from app.security.scope import fdr_visibility

router = APIRouter(tags=["insights"])


@router.get("/alerts", response_model=AlertsOut)
def alerts(response: Response, db: Session = Depends(get_db), auth: AuthContext = Depends(require_authenticated)) -> AlertsOut:
    response.headers["Cache-Control"] = "no-store"
    return build_alerts(db, auth.user)


@router.get("/exceptions", response_model=ExceptionsOut)
def exceptions(response: Response, db: Session = Depends(get_db), auth: AuthContext = Depends(require_authenticated)) -> ExceptionsOut:
    response.headers["Cache-Control"] = "no-store"
    return build_exceptions(db, auth.user)


@router.get("/activity", response_model=ActivityOut)
def activity(
    response: Response, limit: int = Query(10, ge=1, le=50), db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_authenticated),
) -> ActivityOut:
    """The most recent status changes (created, submitted, returned, accepted, rejected) for FDRs the caller
    may see, newest first. Comments and personal names are deliberately left out."""
    response.headers["Cache-Control"] = "no-store"
    rows = db.execute(
        select(FDRStatusHistory, FDR, Institution)
        .join(FDR, FDR.id == FDRStatusHistory.fdr_id).join(Institution, Institution.id == FDR.institution_id)
        .where(fdr_visibility(auth.user))
        .order_by(FDRStatusHistory.occurred_at.desc(), FDRStatusHistory.id.desc()).limit(limit)
    ).all()
    return ActivityOut(items=[
        ActivityItem(occurred_at=h.occurred_at, fdr_id=f.id, fdr_number=f.fdr_number, institution_name=i.name,
                     from_status=h.from_status, to_status=h.to_status, actor_role=h.actor_role)
        for h, f, i in rows
    ])
