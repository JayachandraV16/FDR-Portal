"""Reviewer workload and waiting-time dashboard. Read-only; every figure is derived from existing records.

  GET /review/workload?days=30   AICTE reviewers, approvers and administrators

What is measured, and what is not:
  * "Pending" is FDRs in SUBMITTED now. Waiting time runs from the latest submission (FDR.submitted_at, which is
    set again each time an FDR is resubmitted after correction).
  * Completed work is the status history: decisions taken from SUBMITTED (accepted, rejected, returned for
    correction) inside the chosen window. Turnaround is the time from the latest earlier submission to the
    decision; decisions without both timestamps are excluded and counted, never guessed.
  * There is NO official review deadline in this system. Unless REVIEW_SLA_DAYS is configured, no breach is
    reported at all, only elapsed waiting time.
  * Reviewer assignment does not exist, so assigned and unassigned counts are not available and none is invented.
  * People are identified by role. Names are returned to administrators only.
"""

import statistics
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.api.deps import AuthContext, require_roles
from app.config import Settings, get_settings
from app.db import get_db
from app.models import FDR, FDRStatusHistory, FdrStatus, Role, User
from app.schemas.ops import (
    DecisionTally, PersonTally, RoleTally, SlaStatus, StatusCount, Turnaround, WaitingBucket, WorkloadOut,
)
from app.security.scope import fdr_visibility
from app.timeutil import as_utc, utcnow

router = APIRouter(tags=["workload"])
allowed = require_roles(Role.AICTE_REVIEWER, Role.AICTE_APPROVER, Role.ADMIN)

PERIODS = (7, 30, 90)
MAX_DECISIONS = 50_000
_DECISIONS = {
    FdrStatus.ACCEPTED.value: "Accepted",
    FdrStatus.REJECTED.value: "Rejected",
    FdrStatus.CORRECTION_REQUESTED.value: "Returned for correction",
}
_STATUS_LABELS = {
    "SUBMITTED": "Awaiting review", "CORRECTION_REQUESTED": "Returned for correction",
    "ACCEPTED": "Accepted", "REJECTED": "Rejected",
}
_BUCKETS = (("D2", "Under 2 days", 0, 2), ("D7", "2 to 6 days", 2, 7), ("D14", "7 to 13 days", 7, 14), ("D14P", "14 days or more", 14, None))
TURNAROUND_NOTE = (
    "Time from the latest earlier submission to each decision taken in this period. Decisions with a missing "
    "timestamp are excluded and counted."
)


def _hours(delta: timedelta) -> float:
    return delta.total_seconds() / 3600


@router.get("/review/workload", response_model=WorkloadOut)
def workload(
    response: Response,
    days: int = Query(30, description="Window for completed reviews: 7, 30 or 90 days"),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(allowed),
    settings: Settings = Depends(get_settings),
) -> WorkloadOut:
    response.headers["Cache-Control"] = "no-store"
    if days not in PERIODS:
        raise HTTPException(status_code=422, detail="days must be 7, 30 or 90")
    user = auth.user
    now = utcnow()
    since = now - timedelta(days=days)
    visible = fdr_visibility(user)

    # --- the queue today: one column of timestamps, bucketed here ---
    submitted = db.scalars(select(FDR.submitted_at).where(visible, FDR.status == FdrStatus.SUBMITTED.value)).all()
    pending = len(submitted)
    stamps = [as_utc(s) for s in submitted if s is not None]
    missing = pending - len(stamps)
    ages = [now - s for s in stamps]
    waiting = []
    for key, label, low, high in _BUCKETS:
        count = sum(1 for a in ages if a >= timedelta(days=low) and (high is None or a < timedelta(days=high)))
        waiting.append(WaitingBucket(key=key, label=label, count=count))
    oldest = min(stamps) if stamps else None

    threshold = settings.review_sla_days
    if threshold is None:
        sla = SlaStatus(
            configured=False, threshold_days=None, beyond=None, approaching=None,
            note="No review deadline is configured, so no SLA breach is reported. Only elapsed waiting time is shown.",
        )
    else:
        limit = timedelta(days=threshold)
        sla = SlaStatus(
            configured=True, threshold_days=threshold,
            beyond=sum(1 for a in ages if a > limit),
            approaching=sum(1 for a in ages if limit * 0.8 <= a <= limit),
            note=f"Against the configured threshold of {threshold} days (REVIEW_SLA_DAYS). It is a local setting, not an AICTE rule.",
        )

    # --- decisions in the window, with the submission each one answered, in one query ---
    earlier = aliased(FDRStatusHistory)
    submitted_before = (
        select(func.max(earlier.occurred_at))
        .where(earlier.fdr_id == FDRStatusHistory.fdr_id, earlier.to_status == FdrStatus.SUBMITTED.value,
               earlier.occurred_at <= FDRStatusHistory.occurred_at)
        .correlate(FDRStatusHistory)
        .scalar_subquery()
    )
    rows = db.execute(
        select(FDRStatusHistory.actor_user_id, FDRStatusHistory.actor_role, FDRStatusHistory.to_status,
               FDRStatusHistory.occurred_at, submitted_before)
        .join(FDR, FDR.id == FDRStatusHistory.fdr_id)
        .where(visible, FDRStatusHistory.from_status == FdrStatus.SUBMITTED.value,
               FDRStatusHistory.to_status.in_(list(_DECISIONS)), FDRStatusHistory.occurred_at >= since)
        .order_by(FDRStatusHistory.occurred_at.desc())
        .limit(MAX_DECISIONS)
    ).all()

    tally = {key: 0 for key in _DECISIONS}
    by_role: dict[str, int] = {}
    by_actor: dict = {}
    durations: list[float] = []
    for actor_id, actor_role, to_status, occurred, submitted_on in rows:
        tally[to_status] += 1
        by_role[actor_role] = by_role.get(actor_role, 0) + 1
        by_actor[actor_id] = by_actor.get(actor_id, 0) + 1
        if submitted_on is not None and occurred is not None:
            durations.append(max(_hours(as_utc(occurred) - as_utc(submitted_on)), 0.0))
    excluded = len(rows) - len(durations)
    turnaround = Turnaround(
        measured=len(durations), excluded=excluded,
        average_hours=round(statistics.fmean(durations), 1) if durations else None,
        median_hours=round(statistics.median(durations), 1) if durations else None,
        note=TURNAROUND_NOTE,
    )

    by_person = None
    if user.role == Role.ADMIN.value and by_actor:
        people = {u.id: u for u in db.scalars(select(User).where(User.id.in_(list(by_actor))))}
        by_person = sorted(
            (PersonTally(name=people[i].full_name if i in people else "Unknown", role=people[i].role if i in people else "", count=c)
             for i, c in by_actor.items()),
            key=lambda p: (-p.count, p.name),
        )
    elif user.role == Role.ADMIN.value:
        by_person = []

    counts = dict(db.execute(select(FDR.status, func.count()).where(visible).group_by(FDR.status)).all())
    return WorkloadOut(
        generated_at=now, period_days=days, window_start=since, pending=pending, oldest_pending_at=oldest,
        oldest_pending_days=(now - oldest).days if oldest else None, pending_missing_timestamp=missing,
        waiting=waiting, sla=sla, completed_total=sum(tally.values()),
        completed=[DecisionTally(key=k, label=_DECISIONS[k], count=tally[k]) for k in _DECISIONS],
        turnaround=turnaround,
        by_role=sorted((RoleTally(role=r, count=c) for r, c in by_role.items()), key=lambda t: (-t.count, t.role)),
        by_person=by_person,
        mine=by_actor.get(user.id, 0) if user.role != Role.ADMIN.value else None,
        distribution=[StatusCount(key=k, label=l, count=int(counts.get(k, 0))) for k, l in _STATUS_LABELS.items()],
        assignment_tracked=False,
        assignment_note="Assignment not tracked: reviews are not assigned to individual reviewers, so assigned and unassigned counts are not available.",
        queue_link="/review",
    )
