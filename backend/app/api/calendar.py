"""Maturity calendar. Read-only and derived from the FDRs themselves; nothing is stored.

  GET /calendar?start=&end=&institution_id=&status=   FDR maturity dates in a date range

Visibility is the registry's (`fdr_visibility`), applied before any filter, so a filter can only narrow what the
caller already may see. Dates are the persisted maturity dates; "today" is the server's date (UTC, the same
convention the alerts use). Only a MATURITY date exists in the data: there is no separate renewal deadline, and a
past maturity date is shown as information, not as proof that an FDR is invalid or that renewal was missed.
"""

import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, require_authenticated
from app.db import get_db
from app.insights.derive import LIVE, SOON_DAYS, link_for, today
from app.models import FDR, FdrStatus, Institution, Role
from app.schemas.ops import CalendarEvent, CalendarOut, CalendarSummary
from app.security.scope import fdr_visibility

router = APIRouter(tags=["calendar"])

MAX_RANGE_DAYS = 100  # a month grid needs at most 42 days; three months is the widest list view
MAX_EVENTS = 1000
NOTE = (
    "Dates are the maturity dates entered for each FDR (as submitted, not verified with a bank). The system holds no "
    "separate renewal deadline. A date that has passed is shown for attention only."
)


def marker_for(status: str, days_until: int) -> str:
    """OVERDUE / SOON / UPCOMING apply only to live FDRs; every other status is shown unmarked."""
    if status not in LIVE:
        return "NONE"
    if days_until < 0:
        return "OVERDUE"
    return "SOON" if days_until <= SOON_DAYS else "UPCOMING"


@router.get("/calendar", response_model=CalendarOut)
def calendar(
    response: Response,
    start: date = Query(..., description="First day of the range (inclusive)"),
    end: date = Query(..., description="Last day of the range (inclusive)"),
    institution_id: uuid.UUID | None = Query(None, description="Narrow to one institution (ignored for institution users, who have only their own)"),
    status: FdrStatus | None = Query(None, description="Narrow to one status"),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_authenticated),
) -> CalendarOut:
    response.headers["Cache-Control"] = "no-store"
    if end < start:
        raise HTTPException(status_code=422, detail="end must not be before start")
    if (end - start).days > MAX_RANGE_DAYS:
        raise HTTPException(status_code=422, detail=f"The range may not exceed {MAX_RANGE_DAYS} days")

    now = today()
    user = auth.user
    conditions = [fdr_visibility(user), FDR.maturity_date >= start, FDR.maturity_date <= end]
    if institution_id is not None and user.role != Role.INSTITUTION_USER.value:
        conditions.append(FDR.institution_id == institution_id)
    if status is not None:
        conditions.append(FDR.status == status.value)

    total = db.scalar(select(func.count()).select_from(FDR).where(*conditions)) or 0
    rows = db.execute(
        select(FDR.id, FDR.fdr_number, FDR.maturity_date, FDR.status, Institution.code, Institution.name)
        .join(Institution, Institution.id == FDR.institution_id)
        .where(*conditions)
        .order_by(FDR.maturity_date, FDR.fdr_number_normalized, FDR.id)
        .limit(MAX_EVENTS)
    ).all()
    events = [
        CalendarEvent(
            fdr_id=fdr_id, fdr_number=number, institution_code=code, institution_name=name, maturity_date=maturity,
            status=fdr_status, days_until=(maturity - now).days, marker=marker_for(fdr_status, (maturity - now).days),
            link=link_for(user, fdr_id),
        )
        for fdr_id, number, maturity, fdr_status, code, name in rows
    ]

    # The summary is about live FDRs overall (not the month on screen), in one aggregate query.
    live = [fdr_visibility(user), FDR.status.in_(LIVE)]
    if institution_id is not None and user.role != Role.INSTITUTION_USER.value:
        live.append(FDR.institution_id == institution_id)
    overdue, d7, d30, d90 = db.execute(
        select(
            func.coalesce(func.sum(case((FDR.maturity_date < now, 1), else_=0)), 0),
            func.coalesce(func.sum(case(((FDR.maturity_date >= now) & (FDR.maturity_date <= now + timedelta(days=7)), 1), else_=0)), 0),
            func.coalesce(func.sum(case(((FDR.maturity_date >= now) & (FDR.maturity_date <= now + timedelta(days=30)), 1), else_=0)), 0),
            func.coalesce(func.sum(case(((FDR.maturity_date >= now) & (FDR.maturity_date <= now + timedelta(days=90)), 1), else_=0)), 0),
        ).where(*live)
    ).one()

    return CalendarOut(
        today=now, start=start, end=end, soon_days=SOON_DAYS, total=total, truncated=total > len(events), events=events,
        summary=CalendarSummary(overdue=int(overdue), next_7=int(d7), next_30=int(d30), next_90=int(d90)), note=NOTE,
    )
