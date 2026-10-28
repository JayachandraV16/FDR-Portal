import uuid
from datetime import date, datetime

from pydantic import BaseModel


class AlertItem(BaseModel):
    category: str
    severity: str  # HIGH | MEDIUM | LOW
    fdr_id: uuid.UUID
    fdr_number: str
    institution_code: str
    institution_name: str
    message: str
    date: date | None  # the date the alert is about (for example the maturity date)
    days: int | None  # days from today to `date` (negative: past)
    link: str


class AlertCategory(BaseModel):
    key: str
    label: str
    total: int
    exact: bool  # False: `total` is a lower bound because only a bounded number of records was examined
    shown: int
    available: bool
    note: str | None


class AlertsOut(BaseModel):
    """Computed from current records on every request. These are not stored notifications: nothing is
    sent, remembered or acknowledged, and an alert disappears when its cause does."""

    generated_on: date
    basis: str
    categories: list[AlertCategory]
    items: list[AlertItem]


class ExceptionItem(BaseModel):
    kind: str
    severity: str
    fdr_id: uuid.UUID
    fdr_number: str
    institution_code: str
    institution_name: str
    status: str
    issue: str
    link: str


class ExceptionKind(BaseModel):
    key: str
    label: str
    total: int
    available: bool
    note: str | None


class ExceptionsOut(BaseModel):
    """Derived from the records on every request; there is no separate exception table. `scanned` of
    `eligible_total` records were examined, and `complete` says whether that was all of them."""

    generated_on: date
    scanned: int
    eligible_total: int
    complete: bool
    kinds: list[ExceptionKind]
    items: list[ExceptionItem]


class ReleaseEvent(BaseModel):
    action: str  # release.requested | release.approved | release.rejected
    occurred_at: datetime
    actor_role: str | None
    actor_name: str | None
    reason: str | None


class ReleaseOut(BaseModel):
    """A release/refund REQUEST and its decision. This is an authorisation workflow recorded in the audit
    trail. It is not a payment: this system does not move funds and never says it did."""

    fdr_id: uuid.UUID
    fdr_number: str
    institution_code: str
    institution_name: str
    bank_name: str
    principal_amount: str
    maturity_date: date
    fdr_status: str
    matured: bool
    eligible: bool
    eligibility_note: str
    state: str  # NONE | REQUESTED | APPROVED | REJECTED
    can_request: bool  # for the caller
    can_decide: bool  # for the caller
    history: list[ReleaseEvent]


class ReleaseList(BaseModel):
    items: list[ReleaseOut]
    page: int
    page_size: int
    total: int
    total_pages: int
    note: str


class RegisterRow(BaseModel):
    key: str
    label: str
    count: int
    principal: str | None


class MetricStatus(BaseModel):
    available: bool
    note: str | None = None


class ReportOut(BaseModel):
    generated_on: date
    scope: str  # what the caller's totals cover
    fdr_total: int
    by_status: list[RegisterRow]
    by_institution: list[RegisterRow]
    principal_live_total: str  # SUBMITTED + CORRECTION_REQUESTED + ACCEPTED, exact decimal string
    maturities: list[RegisterRow]  # overdue and the next 30/60/90 days, live records only
    release: list[RegisterRow]
    release_status: MetricStatus
    verification: list[RegisterRow]
    verification_status: MetricStatus
    exceptions: list[RegisterRow]
    exceptions_status: MetricStatus


class ActivityItem(BaseModel):
    occurred_at: datetime
    fdr_id: uuid.UUID
    fdr_number: str
    institution_name: str
    from_status: str | None
    to_status: str
    actor_role: str  # the role that acted; names and comments are not part of this feed


class ActivityOut(BaseModel):
    """Recent workflow status changes for FDRs the caller may see. Release decisions are not included (they
    live in the audit trail), and this is not the audit log."""

    items: list[ActivityItem]
