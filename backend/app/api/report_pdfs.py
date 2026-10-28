"""PDF reports. Each one reuses the report data the portal already derives, under the same visibility rules.

  GET /reports/pdf/summary          FDR summary                          any signed-in role (own scope)
  GET /reports/pdf/institutions     live FDRs by institution             any signed-in role (own scope)
  GET /reports/pdf/maturity         live FDRs by maturity date           any signed-in role   ?days=30|60|90 &institution_id=
  GET /reports/pdf/pending-review   FDRs awaiting a decision             AICTE reviewers and approvers   &institution_id=
  GET /reports/pdf/exceptions       derived exceptions                    AICTE reviewers and approvers
  GET /reports/pdf/audit            audit-trail summary and chain check   administrators and auditors   ?days=7|30|90

Authorisation is the server's: scope comes from the signed-in user (`fdr_visibility`), never from a list the
client sends, and a filter can only narrow it. Filters used are printed on the document. Row counts are capped
(MAX_PDF_ROWS) and the document says when a table was cut; totals always cover every matching record. Each
export is written to the audit trail (kind and row count only). PDFs are not digitally signed.
"""

import logging
import uuid
from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, RequestContext, get_request_context, require_authenticated, require_roles
from app.api.reports import _audit_export, build_report, scope_text
from app.audit import verify_chain
from app.db import get_db
from app.insights.derive import LIVE, build_exceptions, today
from app.insights.pdf import ReportSpec, Section, inr, render
from app.models import FDR, AuditLog, Bank, FdrStatus, Institution, Role
from app.security.scope import fdr_visibility
from app.timeutil import as_utc, utcnow

router = APIRouter(tags=["reports"])
log = logging.getLogger("fdr.reports")

reviewers = require_roles(Role.AICTE_REVIEWER, Role.AICTE_APPROVER)
auditors = require_roles(Role.ADMIN, Role.AUDITOR)
MAX_PDF_ROWS = 2000
AUDIT_EVENT_ROWS = 200
_DATE = "%d %b %Y"


def _deliver(db: Session, ctx: RequestContext, auth: AuthContext, kind: str, spec: ReportSpec, rows: int) -> Response:
    try:
        pdf = render(spec)
    except Exception as exc:  # the cause is logged by type only: report data must not reach the log
        log.error("pdf generation failed (%s) for %s", type(exc).__name__, kind)
        raise HTTPException(status_code=500, detail="The PDF could not be generated. Please try again.") from None
    _audit_export(db, ctx, auth, f"{kind}.pdf", rows)
    return Response(
        content=pdf, media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="fdr-{kind}-{today().isoformat()}.pdf"',
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        },
    )


def _institution_filter(db: Session, auth: AuthContext, institution_id: uuid.UUID | None) -> tuple[list, list[str]]:
    """(extra conditions, filter descriptions). Institution users have only their own institution, so the filter
    is ignored for them; for others it narrows an already-scoped query."""
    if institution_id is None or auth.user.role == Role.INSTITUTION_USER.value:
        return [], []
    institution = db.get(Institution, institution_id)
    return [FDR.institution_id == institution_id], [f"Institution: {institution.name if institution else 'unknown institution'}"]


def _money_total(db: Session, conditions: list) -> tuple[int, Decimal]:
    """Count and exact principal total of EVERY matching record (not just the rows that fit in the document)."""
    amounts = [Decimal(str(a)) for a in db.scalars(select(FDR.principal_amount).where(*conditions))]
    return len(amounts), sum(amounts, Decimal(0))


def _fdr_rows(db: Session, conditions: list, order: list):
    rows = db.execute(
        select(FDR, Institution, Bank).join(Institution, Institution.id == FDR.institution_id).join(Bank, Bank.id == FDR.bank_id)
        .where(*conditions).order_by(*order).limit(MAX_PDF_ROWS + 1)
    ).all()
    return rows[:MAX_PDF_ROWS], len(rows) > MAX_PDF_ROWS


def _cut_note(shown: int, total: int, truncated: bool) -> list[str]:
    return [f"The table lists the first {shown:,} of {total:,} matching FDRs; the summary figures cover all {total:,}."] if truncated else []


# ---- summary and institutions -----------------------------------------------------------------------------------
@router.get("/reports/pdf/summary")
def summary_pdf(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context), auth: AuthContext = Depends(require_authenticated)) -> Response:
    report = build_report(db, auth.user)
    maturity = {r.key: r.count for r in report.maturities}
    spec = ReportSpec(
        title="FDR summary report", generated_at=utcnow(), scope=report.scope,
        summary=[("FDRs in scope", f"{report.fdr_total:,}"), ("Principal of live FDRs", inr(report.principal_live_total)),
                 ("Maturing within 30 days", f"{maturity.get('D30', 0):,}"), ("Past maturity (live)", f"{maturity.get('OVERDUE', 0):,}")],
        sections=[
            Section("FDRs by status", header=["Status", "FDRs", "Principal"], weights=[3, 1, 2], right=(1, 2),
                    rows=[[r.label, f"{r.count:,}", inr(r.principal)] for r in report.by_status],
                    total_row=["All statuses", f"{report.fdr_total:,}", ""] if report.by_status else None, empty="No FDRs in scope."),
            Section("Upcoming maturities (live FDRs)", note="Live means submitted, returned for correction or accepted.",
                    header=["Period", "FDRs", "Principal"], weights=[3, 1, 2], right=(1, 2),
                    rows=[[r.label, f"{r.count:,}", inr(r.principal)] for r in report.maturities]),
            *([Section("Release / refund requests (matured, accepted FDRs)", note=report.release_status.note, header=["State", "FDRs"], weights=[4, 1], right=(1,),
                       rows=[[r.label, f"{r.count:,}"] for r in report.release])] if report.release_status.available else []),
        ],
        notes=["Principal amounts are as submitted by institutions."],
    )
    return _deliver(db, ctx, auth, "summary", spec, report.fdr_total)


@router.get("/reports/pdf/institutions")
def institutions_pdf(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context), auth: AuthContext = Depends(require_authenticated)) -> Response:
    report = build_report(db, auth.user)
    live = sum(r.count for r in report.by_institution)
    spec = ReportSpec(
        title="Institution-wise summary", generated_at=utcnow(), scope=report.scope,
        summary=[("Institutions with live FDRs", f"{len(report.by_institution):,}"), ("Live FDRs", f"{live:,}"), ("Principal of live FDRs", inr(report.principal_live_total))],
        sections=[Section("Live FDRs by institution", note="Live means submitted, returned for correction or accepted. Most FDRs first.",
                          header=["Institution", "Live FDRs", "Principal"], weights=[5, 1, 2], right=(1, 2),
                          rows=[[r.label, f"{r.count:,}", inr(r.principal)] for r in report.by_institution],
                          total_row=["Total", f"{live:,}", inr(report.principal_live_total)] if report.by_institution else None,
                          empty="No live FDRs in scope.")],
        notes=["Principal amounts are as submitted by institutions."],
    )
    return _deliver(db, ctx, auth, "institutions", spec, len(report.by_institution))


# ---- maturity ----------------------------------------------------------------------------------------------------
@router.get("/reports/pdf/maturity")
def maturity_pdf(
    days: int = Query(90, description="Include live FDRs maturing within this many days (30, 60 or 90); past-maturity ones are always included"),
    institution_id: uuid.UUID | None = Query(None),
    db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context), auth: AuthContext = Depends(require_authenticated),
) -> Response:
    if days not in (30, 60, 90):
        raise HTTPException(status_code=422, detail="days must be 30, 60 or 90")
    now = today()
    extra, filters = _institution_filter(db, auth, institution_id)
    conditions = [fdr_visibility(auth.user), FDR.status.in_(LIVE), FDR.maturity_date <= now + timedelta(days=days), *extra]
    total, amount = _money_total(db, conditions)
    rows, truncated = _fdr_rows(db, conditions, [FDR.maturity_date, FDR.fdr_number_normalized, FDR.id])
    past = db.scalar(select(func.count()).select_from(FDR).where(*conditions, FDR.maturity_date < now)) or 0
    spec = ReportSpec(
        title="Maturity report", generated_at=utcnow(), scope=scope_text(auth.user.role), wide=True,
        filters=[f"Live FDRs maturing within {days} days of {now.strftime(_DATE)}, plus any already past maturity", *filters],
        summary=[("FDRs listed", f"{total:,}"), ("Already past maturity", f"{past:,}"), ("Principal (as submitted)", inr(amount))],
        sections=[Section(
            "FDRs by maturity date", note="Soonest first. Days is counted from the report date; a negative number means the date has passed. A past date is shown for attention only.",
            header=["FDR number", "Institution", "Bank", "Maturity date", "Days", "Status", "Principal"], weights=[2, 3, 2.5, 1.6, 0.9, 1.8, 2], right=(4, 6),
            rows=[[f.fdr_number, i.name, b.name, f.maturity_date.strftime(_DATE), (f.maturity_date - now).days, f.status.replace("_", " ").title(), inr(f.principal_amount)] for f, i, b in rows],
            empty="No live FDR matches.",
        )],
        notes=[*_cut_note(len(rows), total, truncated), "Only a maturity date exists in the records; the portal holds no separate renewal deadline."],
    )
    return _deliver(db, ctx, auth, "maturity", spec, len(rows))


# ---- pending review ---------------------------------------------------------------------------------------------
@router.get("/reports/pdf/pending-review")
def pending_review_pdf(
    institution_id: uuid.UUID | None = Query(None),
    db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context), auth: AuthContext = Depends(reviewers),
) -> Response:
    now = utcnow()
    extra, filters = _institution_filter(db, auth, institution_id)
    conditions = [fdr_visibility(auth.user), FDR.status == FdrStatus.SUBMITTED.value, *extra]
    total, amount = _money_total(db, conditions)
    rows, truncated = _fdr_rows(db, conditions, [FDR.submitted_at, FDR.id])
    waits = [(now - as_utc(f.submitted_at)).days for f, _, _ in rows if f.submitted_at is not None]
    spec = ReportSpec(
        title="Pending review report", generated_at=now, scope=scope_text(auth.user.role), wide=True,
        filters=["Status: Submitted (awaiting a decision)", *filters],
        summary=[("FDRs awaiting review", f"{total:,}"), ("Longest wait", f"{max(waits)} days" if waits else "None"), ("Principal (as submitted)", inr(amount))],
        sections=[Section(
            "FDRs awaiting a decision", note="Oldest submission first. Waiting time runs from the latest submission.",
            header=["FDR number", "Institution", "Bank", "Submitted", "Days waiting", "Principal"], weights=[2, 3.5, 2.5, 1.6, 1.2, 2], right=(4, 5),
            rows=[[f.fdr_number, i.name, b.name, f.submitted_at.strftime(_DATE) if f.submitted_at else "Not recorded",
                   (now - as_utc(f.submitted_at)).days if f.submitted_at else "n/a", inr(f.principal_amount)] for f, i, b in rows],
            empty="No FDR is awaiting review.",
        )],
        notes=[*_cut_note(len(rows), total, truncated), "Assignment to individual reviewers is not tracked."],
    )
    return _deliver(db, ctx, auth, "pending-review", spec, len(rows))


# ---- exceptions ------------------------------------------------------------------------------------------------
@router.get("/reports/pdf/exceptions")
def exceptions_pdf(db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context), auth: AuthContext = Depends(reviewers)) -> Response:
    result = build_exceptions(db, auth.user)
    shown = result.items[:MAX_PDF_ROWS]
    unavailable = [k.label for k in result.kinds if not k.available]
    notes = []
    if not result.complete:
        notes.append(f"Based on {result.scanned:,} of {result.eligible_total:,} open records, so counts are a lower bound.")
    if unavailable:
        notes.append(f"Not available to you or not yet computable: {', '.join(unavailable)}.")
    if len(result.items) > len(shown):
        notes.append(f"The table lists the first {len(shown):,} of {len(result.items):,} exceptions.")
    notes.append("A mismatch is not a finding of fraud; it means a person should look. Exceptions are derived on each request and nothing here is bank-verified.")
    spec = ReportSpec(
        title="Exceptions and discrepancies report", generated_at=utcnow(), scope=scope_text(auth.user.role), wide=True,
        summary=[("Exceptions found", f"{len(result.items):,}"), ("Open records examined", f"{result.scanned:,} of {result.eligible_total:,}")],
        sections=[
            Section("Exceptions by kind", header=["Kind", "Count"], weights=[5, 1], right=(1,), rows=[[k.label, f"{k.total:,}"] for k in result.kinds if k.available], empty="No exception kind could be computed."),
            Section("Exceptions", note="Most serious first.", header=["Severity", "Kind", "FDR number", "Institution", "FDR status", "Issue"], weights=[1, 1.6, 1.6, 2.6, 1.4, 5],
                    rows=[[e.severity.title(), e.kind.replace("_", " ").title(), e.fdr_number, e.institution_name, e.status.replace("_", " ").title(), e.issue] for e in shown],
                    empty="No exceptions were found in the records examined."),
        ],
        notes=notes,
    )
    return _deliver(db, ctx, auth, "exceptions", spec, len(shown))


# ---- audit -------------------------------------------------------------------------------------------------------
@router.get("/reports/pdf/audit")
def audit_pdf(
    days: int = Query(30, description="Window in days: 7, 30 or 90"),
    db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context), auth: AuthContext = Depends(auditors),
) -> Response:
    if days not in (7, 30, 90):
        raise HTTPException(status_code=422, detail="days must be 7, 30 or 90")
    now = utcnow()
    since = now - timedelta(days=days)
    counts = db.execute(
        select(AuditLog.action, AuditLog.outcome, func.count()).where(AuditLog.occurred_at >= since).group_by(AuditLog.action, AuditLog.outcome).order_by(AuditLog.action, AuditLog.outcome)
    ).all()
    total = sum(c for _, _, c in counts)
    events = db.execute(select(AuditLog).where(AuditLog.occurred_at >= since).order_by(AuditLog.seq.desc()).limit(AUDIT_EVENT_ROWS)).scalars().all()
    chain = verify_chain(db)
    verdict = f"Intact: {chain.checked:,} events re-checked" if chain.ok else f"BROKEN at event {chain.first_bad_seq} ({chain.reason}); {chain.checked:,} events checked before it"
    spec = ReportSpec(
        title="Audit trail report", generated_at=now, scope="The whole audit trail (administrators and auditors only).", wide=True,
        filters=[f"Events from the last {days} days (since {since.strftime(_DATE)})"],
        summary=[("Events in the period", f"{total:,}"), ("Hash chain", verdict)],
        sections=[
            Section("Events by action and outcome", header=["Action", "Outcome", "Events"], weights=[5, 1.5, 1], right=(2,), rows=[[a, o, f"{c:,}"] for a, o, c in counts], total_row=["Total", "", f"{total:,}"] if counts else None, empty="No events in this period."),
            Section(f"Most recent {AUDIT_EVENT_ROWS} events", note="Newest first. People are identified by role only; no addresses, tokens or free text are included.",
                    header=["Event", "Time (UTC)", "Action", "Outcome", "Actor role", "Subject"], weights=[0.8, 2, 3.2, 1.2, 1.8, 1.4],
                    rows=[[e.seq, e.occurred_at.strftime("%d %b %Y %H:%M:%S"), e.action, e.outcome, e.actor_role or "-", e.entity_type or "-"] for e in events], empty="No events in this period."),
        ],
        notes=["The hash chain check recomputes every stored event hash from the first event; it shows that the recorded trail has not been altered since it was written, not that the underlying facts are correct."],
    )
    return _deliver(db, ctx, auth, "audit", spec, len(events))
