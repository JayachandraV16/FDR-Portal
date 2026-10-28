"""Reports: aggregates and CSV exports over the caller's complete authorised dataset.

  GET /reports/summary          counts, principal totals, maturities, release, verification, exceptions
  GET /reports/fdrs.csv         every FDR the caller may see (not one page of the registry)
  GET /reports/exceptions.csv   the derived exceptions (AICTE reviewers and approvers only)

Totals are computed over EVERYTHING the caller may see, in one pass, with exact decimals; they are never
a sum over a paginated API response. Sections that cannot be computed reliably (the document migrations
are not applied, or the role may not see extraction data) are reported as unavailable with a reason, not
as zero. Exports are restricted by the same visibility rule as the registry, neutralise spreadsheet
formula injection, and each export is written to the audit trail (row count only).
"""

import csv
import io
from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, RequestContext, get_request_context, require_authenticated, require_roles
from app.audit import AuditOutcome, write_audit_event
from app.db import get_db
from app.extraction.summary import document_checks, document_tables_ready
from app.insights import release_state
from app.insights.derive import LIVE, REVIEW_ROLES, SCAN_LIMIT, build_exceptions, today
from app.models import FDR, Bank, FdrStatus, Institution, Role
from app.schemas.insights import MetricStatus, RegisterRow, ReportOut
from app.security.scope import fdr_visibility

router = APIRouter(tags=["reports"])
reviewers = require_roles(Role.AICTE_REVIEWER, Role.AICTE_APPROVER)
MAX_EXPORT_ROWS = 100_000
_STATUS_LABELS = {
    "DRAFT": "Draft", "SUBMITTED": "Submitted", "CORRECTION_REQUESTED": "Correction requested",
    "ACCEPTED": "Accepted", "REJECTED": "Rejected", "WITHDRAWN": "Withdrawn",
}


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def scope_text(role: str) -> str:
    """Which records a role's reports cover (also printed on PDF reports)."""
    if role == Role.INSTITUTION_USER.value:
        return "Your institution's FDRs, in every status."
    return "Every institution's FDRs that have left draft (submitted, returned, accepted or rejected)."


def build_report(db: Session, user) -> ReportOut:
    now = today()
    visible = fdr_visibility(user)
    by_status: dict[str, list] = {}
    by_institution: dict = {}
    maturities = {"OVERDUE": [0, Decimal(0)], "D30": [0, Decimal(0)], "D60": [0, Decimal(0)], "D90": [0, Decimal(0)]}
    live_total = Decimal(0)
    total = 0
    matured_accepted = []
    live_ids = []
    for fdr_id, status, institution_id, principal, maturity in db.execute(
        select(FDR.id, FDR.status, FDR.institution_id, FDR.principal_amount, FDR.maturity_date).where(visible)
    ):
        amount = Decimal(principal)
        total += 1
        slot = by_status.setdefault(status, [0, Decimal(0)])
        slot[0] += 1
        slot[1] += amount
        if status not in LIVE:
            continue
        live_ids.append(fdr_id)
        live_total += amount
        inst = by_institution.setdefault(institution_id, [0, Decimal(0)])
        inst[0] += 1
        inst[1] += amount
        days = (maturity - now).days
        bucket = "OVERDUE" if days < 0 else "D30" if days <= 30 else "D60" if days <= 60 else "D90" if days <= 90 else None
        if bucket:
            maturities[bucket][0] += 1
            maturities[bucket][1] += amount
        if status == FdrStatus.ACCEPTED.value and maturity <= now:
            matured_accepted.append(fdr_id)

    names = {i.id: i for i in db.scalars(select(Institution).where(Institution.id.in_(list(by_institution))))} if by_institution else {}
    status_rows = [RegisterRow(key=s, label=_STATUS_LABELS.get(s, s), count=c, principal=_money(p)) for s, (c, p) in sorted(by_status.items())]
    institution_rows = sorted(
        (RegisterRow(key=str(i), label=f"{names[i].name} ({names[i].code})" if i in names else str(i), count=c, principal=_money(p))
         for i, (c, p) in by_institution.items()),
        key=lambda r: (-r.count, r.label),
    )
    labels = {"OVERDUE": "Past maturity", "D30": "Within 30 days", "D60": "31 to 60 days", "D90": "61 to 90 days"}
    maturity_rows = [RegisterRow(key=k, label=labels[k], count=c, principal=_money(p)) for k, (c, p) in maturities.items()]

    states = release_state.release_states(db, matured_accepted)
    release_rows = [
        RegisterRow(key=key, label=label, count=sum(1 for s in states.values() if s == key), principal=None)
        for key, label in [(release_state.NONE, "Matured, no request yet"), (release_state.REQUESTED, "Release requested"),
                           (release_state.APPROVED, "Release approved (no funds moved by this system)"),
                           (release_state.REJECTED, "Release request rejected")]
    ]

    verification_rows: list[RegisterRow] = []
    exception_rows: list[RegisterRow] = []
    if user.role not in REVIEW_ROLES:
        verification_status = MetricStatus(available=False, note="Document verification results are visible to AICTE reviewers and approvers only.")
    elif not document_tables_ready(db):
        verification_status = MetricStatus(available=False, note="Unavailable until the document and extraction migrations are applied to this database.")
    else:
        scan = db.execute(select(FDR).where(visible, FDR.status.in_(LIVE)).order_by(FDR.maturity_date, FDR.id).limit(SCAN_LIMIT)).scalars().all()
        summaries = document_checks(db, scan)
        tally = {"MISMATCH": 0, "CLEAN": 0, "NOT_ANALYZED": 0, "PENDING": 0, "FAILED": 0, "NO_DOCUMENTS": 0}
        for s in summaries.values():
            key = ("MISMATCH" if s.mismatch_count else "CLEAN") if s.status == "ANALYZED" else s.status
            tally[key] += 1
        verification_rows = [RegisterRow(key=k, label=l, count=tally[k], principal=None) for k, l in [
            ("MISMATCH", "At least one mismatch with the submitted record"), ("CLEAN", "Analyzed, no mismatch found"),
            ("NOT_ANALYZED", "Documents not analyzed"), ("PENDING", "Check in progress"), ("FAILED", "Check failed"),
            ("NO_DOCUMENTS", "No documents")]]
        verification_status = MetricStatus(
            available=True,
            note=None if len(scan) < SCAN_LIMIT else f"Based on the first {SCAN_LIMIT} open records only; the rest were not examined.",
        )
    ex = build_exceptions(db, user)
    exception_rows = [RegisterRow(key=k.key, label=k.label, count=k.total, principal=None) for k in ex.kinds if k.available]
    unavailable = [k.label for k in ex.kinds if not k.available]
    exceptions_status = MetricStatus(
        available=True,
        note=" ".join(filter(None, [
            None if ex.complete else f"Based on {ex.scanned} of {ex.eligible_total} open records.",
            f"Not available to you or not yet computable: {', '.join(unavailable)}." if unavailable else None,
        ])) or None,
    )
    scope = scope_text(user.role)
    return ReportOut(
        generated_on=now, scope=scope, fdr_total=total, by_status=status_rows, by_institution=institution_rows,
        principal_live_total=_money(live_total), maturities=maturity_rows, release=release_rows,
        release_status=MetricStatus(available=True, note="Release state is read from the audit trail; no release table exists yet."),
        verification=verification_rows, verification_status=verification_status,
        exceptions=exception_rows, exceptions_status=exceptions_status,
    )


@router.get("/reports/summary", response_model=ReportOut)
def summary(response: Response, db: Session = Depends(get_db), auth: AuthContext = Depends(require_authenticated)) -> ReportOut:
    response.headers["Cache-Control"] = "no-store"
    return build_report(db, auth.user)


def _safe(value) -> str:
    """A CSV cell that a spreadsheet will not treat as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _csv_response(header: list[str], rows, filename: str) -> tuple[Response, int]:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header)
    count = 0
    for row in rows:
        writer.writerow([_safe(c) for c in row])
        count += 1
    return Response(
        content="﻿" + buffer.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    ), count


def _audit_export(db, ctx, auth, kind: str, count: int) -> None:
    write_audit_event(
        db, action="report.export", outcome=AuditOutcome.SUCCESS, actor_user_id=auth.user.id, actor_role=auth.user.role,
        entity_type="report", entity_id=None, ip_address=ctx.ip_address, request_id=ctx.request_id,
        details={"filters": [kind], "result_count": count},
    )
    db.commit()


@router.get("/reports/fdrs.csv")
def fdrs_csv(
    request: Request, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(require_authenticated),
) -> Response:
    rows = db.execute(
        select(FDR, Institution, Bank).join(Institution, Institution.id == FDR.institution_id).join(Bank, Bank.id == FDR.bank_id)
        .where(fdr_visibility(auth.user)).order_by(Institution.code, FDR.maturity_date, FDR.id).limit(MAX_EXPORT_ROWS)
    ).all()
    response, count = _csv_response(
        ["FDR number", "Institution code", "Institution", "Bank", "Branch", "Status", "Principal (INR)", "Interest rate (%)",
         "Issue date", "Maturity date", "Submitted at"],
        ([f.fdr_number, i.code, i.name, b.name, f.branch_name, f.status, f.principal_amount, f.interest_rate_pct,
          f.issue_date.isoformat(), f.maturity_date.isoformat(), f.submitted_at.isoformat() if f.submitted_at else ""]
         for f, i, b in rows),
        f"fdr-report-{today().isoformat()}.csv",
    )
    _audit_export(db, ctx, auth, "fdrs", count)
    return response


@router.get("/reports/exceptions.csv")
def exceptions_csv(
    request: Request, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(reviewers),
) -> Response:
    ex = build_exceptions(db, auth.user)
    response, count = _csv_response(
        ["Severity", "Kind", "FDR number", "Institution code", "Institution", "FDR status", "Issue"],
        ([e.severity, e.kind, e.fdr_number, e.institution_code, e.institution_name, e.status, e.issue] for e in ex.items),
        f"exceptions-{today().isoformat()}.csv",
    )
    _audit_export(db, ctx, auth, "exceptions", count)
    return response
