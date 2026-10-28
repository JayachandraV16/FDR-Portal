"""Response shapes for the maturity calendar and the reviewer workload dashboard (both read-only, derived)."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel


class CalendarEvent(BaseModel):
    fdr_id: uuid.UUID
    fdr_number: str
    institution_code: str
    institution_name: str
    maturity_date: date
    status: str
    days_until: int  # from the server's today to the maturity date; negative once it has passed
    marker: str  # OVERDUE | SOON | UPCOMING | NONE (see build_calendar): a reading aid, never a verdict
    link: str


class CalendarSummary(BaseModel):
    """Live FDRs (submitted, returned or accepted) by distance to their maturity date, over everything the caller
    may see and independent of the month on screen."""

    overdue: int  # maturity date already passed
    next_7: int
    next_30: int
    next_90: int


class CalendarOut(BaseModel):
    today: date
    start: date
    end: date
    soon_days: int
    total: int  # events in the requested range
    truncated: bool  # more events matched than were returned
    events: list[CalendarEvent]
    summary: CalendarSummary
    note: str


class WaitingBucket(BaseModel):
    key: str
    label: str
    count: int


class DecisionTally(BaseModel):
    key: str  # ACCEPTED | REJECTED | CORRECTION_REQUESTED
    label: str
    count: int


class RoleTally(BaseModel):
    role: str
    count: int


class PersonTally(BaseModel):
    name: str
    role: str
    count: int


class Turnaround(BaseModel):
    measured: int  # decisions that had both a submission time and a decision time
    excluded: int  # decisions left out because a timestamp was missing
    average_hours: float | None
    median_hours: float | None
    note: str


class SlaStatus(BaseModel):
    configured: bool
    threshold_days: int | None
    beyond: int | None  # None when no threshold is configured
    approaching: int | None
    note: str


class StatusCount(BaseModel):
    key: str
    label: str
    count: int


class WorkloadOut(BaseModel):
    generated_at: datetime
    period_days: int
    window_start: datetime
    pending: int  # FDRs in SUBMITTED now
    oldest_pending_at: datetime | None
    oldest_pending_days: int | None
    pending_missing_timestamp: int
    waiting: list[WaitingBucket]
    sla: SlaStatus
    completed_total: int
    completed: list[DecisionTally]
    turnaround: Turnaround
    by_role: list[RoleTally]  # decisions in the period by the role that took them
    by_person: list[PersonTally] | None  # administrators only; None for everyone else
    mine: int | None  # the caller's own decisions in the period (reviewer roles)
    distribution: list[StatusCount]
    assignment_tracked: bool
    assignment_note: str
    queue_link: str
