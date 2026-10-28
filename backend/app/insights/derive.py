"""Alerts and exceptions, computed from the records a user may see.

Both are DERIVED on every request from the authoritative tables (FDRs, documents, stored extraction
results, the release audit events). Nothing is stored, so there is no second source of truth that could
disagree with the records, and nothing is sent anywhere.

Honesty rules:
  * Everything is restricted by the same visibility rule as the FDR registry (`fdr_visibility`).
  * Counts that come from a SQL COUNT are exact. Anything computed in Python over a bounded scan says so:
    `exact` / `complete` is False when the bound was reached, and the total is then a lower bound.
  * Document-derived findings need the document/extraction tables. If those migrations are not applied,
    the affected category is reported as unavailable (never as zero).
  * Extraction results are only used for roles allowed to see them (reviewers and approvers). A missing
    or ambiguous result is never presented as a confirmed match, and no result is called verification.
"""

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.extraction.compare import FIELD_LABELS
from app.extraction.summary import document_findings, document_tables_ready
from app.insights import release_state
from app.models import FDR, FdrStatus, Institution, Role, User
from app.schemas.fdrs import SUBMISSION_REQUIRED_FIELDS
from app.schemas.insights import AlertCategory, AlertItem, AlertsOut, ExceptionItem, ExceptionKind, ExceptionsOut
from app.security.scope import fdr_visibility
from app.timeutil import utcnow

LIVE = (FdrStatus.SUBMITTED.value, FdrStatus.CORRECTION_REQUESTED.value, FdrStatus.ACCEPTED.value)
SOON_DAYS = 30
ITEMS_PER_CATEGORY = 50
OVERDUE_SCAN = 2000  # records examined to decide which overdue FDRs already have an approved release
SCAN_LIMIT = 500  # records examined for exceptions
_SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
REVIEW_ROLES = (Role.AICTE_REVIEWER.value, Role.AICTE_APPROVER.value)
DOCUMENT_ROLES = (Role.INSTITUTION_USER.value, *REVIEW_ROLES)  # the roles the document endpoints allow


def today() -> date:
    return utcnow().date()


def link_for(user: User, fdr_id) -> str:
    """Where this role can open the record: the review page for reviewers, the record page for the owning
    institution, and the registry for roles that have no record page."""
    if user.role in REVIEW_ROLES:
        return f"/review/{fdr_id}"
    if user.role == Role.INSTITUTION_USER.value:
        return f"/fdrs/{fdr_id}/edit"
    return "/fdrs"


def _joined():
    return select(FDR, Institution).join(Institution, Institution.id == FDR.institution_id)


def _soon_severity(days: int) -> str:
    return "HIGH" if days <= 7 else "MEDIUM" if days <= 14 else "LOW"


def _item(user, category, severity, fdr, institution, message, on=None, now=None) -> AlertItem:
    return AlertItem(
        category=category, severity=severity, fdr_id=fdr.id, fdr_number=fdr.fdr_number, institution_code=institution.code,
        institution_name=institution.name, message=message, date=on, days=None if on is None else (on - now).days,
        link=link_for(user, fdr.id),
    )


def build_alerts(db: Session, user: User) -> AlertsOut:
    now = today()
    visible = fdr_visibility(user)
    categories: list[AlertCategory] = []
    items: list[AlertItem] = []
    is_review = user.role in REVIEW_ROLES
    is_institution = user.role == Role.INSTITUTION_USER.value

    def category(key, label, total, shown, *, exact=True, available=True, note=None):
        categories.append(AlertCategory(key=key, label=label, total=total, exact=exact, shown=shown, available=available, note=note))

    # ---- maturities in the next 30 days (live records) ----
    soon = (FDR.status.in_(LIVE), FDR.maturity_date >= now, FDR.maturity_date <= now + timedelta(days=SOON_DAYS))
    total = db.scalar(select(func.count()).select_from(FDR).where(visible, *soon)) or 0
    rows = db.execute(_joined().where(visible, *soon).order_by(FDR.maturity_date, FDR.id).limit(ITEMS_PER_CATEGORY)).all()
    for fdr, inst in rows:
        days = (fdr.maturity_date - now).days
        items.append(_item(user, "MATURITY_SOON", _soon_severity(days), fdr, inst,
                           "Matures today" if days == 0 else f"Matures in {days} day{'s' if days != 1 else ''}", fdr.maturity_date, now))
    category("MATURITY_SOON", f"Maturing within {SOON_DAYS} days", total, len(rows))

    # ---- past maturity and not yet released ----
    past = (FDR.status.in_(LIVE), FDR.maturity_date < now)
    candidates = db.execute(_joined().where(visible, *past).order_by(FDR.maturity_date, FDR.id).limit(OVERDUE_SCAN)).all()
    states = release_state.release_states(db, [f.id for f, _ in candidates])
    overdue = [(f, i) for f, i in candidates if states[f.id] != release_state.APPROVED]
    for fdr, inst in overdue[:ITEMS_PER_CATEGORY]:
        late = (now - fdr.maturity_date).days
        items.append(_item(user, "OVERDUE", "HIGH", fdr, inst,
                           f"{late} day{'s' if late != 1 else ''} past maturity ({fdr.status.replace('_', ' ').lower()}); no approved release", fdr.maturity_date, now))
    category("OVERDUE", "Past maturity, no approved release", len(overdue), min(len(overdue), ITEMS_PER_CATEGORY),
             exact=len(candidates) < OVERDUE_SCAN,
             note=None if len(candidates) < OVERDUE_SCAN else f"Only the {OVERDUE_SCAN} longest-overdue records were examined; the true total may be higher.")

    # ---- awaiting review / returned for correction ----
    if is_review or is_institution:
        for key, label, status, severity, text in [
            ("AWAITING_REVIEW", "Submitted, awaiting AICTE review", FdrStatus.SUBMITTED.value, "MEDIUM" if is_review else "LOW",
             "Waiting for AICTE review" if is_review else "Submitted; waiting for AICTE review"),
            ("CORRECTION_REQUESTED", "Returned for correction", FdrStatus.CORRECTION_REQUESTED.value, "HIGH" if is_institution else "LOW",
             "Corrections requested: update and resubmit" if is_institution else "Returned to the institution; waiting for corrections"),
        ]:
            cond = (FDR.status == status,)
            total = db.scalar(select(func.count()).select_from(FDR).where(visible, *cond)) or 0
            order = FDR.submitted_at if status == FdrStatus.SUBMITTED.value else FDR.updated_at
            rows = db.execute(_joined().where(visible, *cond).order_by(order, FDR.id).limit(ITEMS_PER_CATEGORY)).all()
            for fdr, inst in rows:
                items.append(_item(user, key, severity, fdr, inst, text, fdr.maturity_date, now))
            category(key, label, total, len(rows))

    # ---- release requests waiting for a decision ----
    if is_review:
        matured = db.execute(
            _joined().where(visible, FDR.status == FdrStatus.ACCEPTED.value, FDR.maturity_date <= now).order_by(FDR.maturity_date, FDR.id).limit(OVERDUE_SCAN)
        ).all()
        s = release_state.release_states(db, [f.id for f, _ in matured])
        waiting = [(f, i) for f, i in matured if s[f.id] == release_state.REQUESTED]
        for fdr, inst in waiting[:ITEMS_PER_CATEGORY]:
            items.append(_item(user, "RELEASE_REQUESTED", "MEDIUM", fdr, inst, "Release requested: awaiting an approver's decision", fdr.maturity_date, now))
        category("RELEASE_REQUESTED", "Release requests awaiting a decision", len(waiting), min(len(waiting), ITEMS_PER_CATEGORY),
                 exact=len(matured) < OVERDUE_SCAN)

    # ---- documents that could not be analysed (reviewers only) ----
    if is_review:
        if not document_tables_ready(db):
            category("EXTRACTION_FAILED", "Document processing failed", 0, 0, available=False,
                     note="Unavailable until the document and extraction migrations are applied to this database.")
        else:
            scan = db.execute(
                _joined().where(visible, FDR.status.in_((FdrStatus.SUBMITTED.value, FdrStatus.CORRECTION_REQUESTED.value)))
                .order_by(FDR.submitted_at, FDR.id).limit(SCAN_LIMIT)
            ).all()
            found = document_findings(db, [f for f, _ in scan])
            failed = [(f, i) for f, i in scan if found[f.id].failed_documents > 0]
            for fdr, inst in failed[:ITEMS_PER_CATEGORY]:
                n = found[fdr.id].failed_documents
                items.append(_item(user, "EXTRACTION_FAILED", "MEDIUM", fdr, inst,
                                   f"{n} document{'s' if n != 1 else ''} could not be analysed; review by eye or retry", None, now))
            category("EXTRACTION_FAILED", "Document processing failed", len(failed), min(len(failed), ITEMS_PER_CATEGORY),
                     exact=len(scan) < SCAN_LIMIT,
                     note=None if len(scan) < SCAN_LIMIT else f"Only the first {SCAN_LIMIT} open records were examined.")

    items.sort(key=lambda a: (_SEVERITY_ORDER[a.severity], a.date or date.max, a.fdr_number))
    return AlertsOut(generated_on=now, basis="Computed from current records on each request; not stored notifications.",
                     categories=categories, items=items)


def _label(names) -> str:
    return ", ".join(FIELD_LABELS.get(n, n).lower() for n in names)


def build_exceptions(db: Session, user: User) -> ExceptionsOut:
    now = today()
    visible = fdr_visibility(user)
    eligible_total = db.scalar(select(func.count()).select_from(FDR).where(visible, FDR.status.in_(LIVE))) or 0
    scan = db.execute(_joined().where(visible, FDR.status.in_(LIVE)).order_by(FDR.maturity_date, FDR.id).limit(SCAN_LIMIT)).all()
    fdrs = [f for f, _ in scan]
    complete = len(scan) >= eligible_total

    can_see_documents = user.role in DOCUMENT_ROLES
    can_see_results = user.role in REVIEW_ROLES
    tables = document_tables_ready(db) if can_see_documents else False
    findings = document_findings(db, fdrs) if tables else {}
    states = release_state.release_states(db, [f.id for f in fdrs if f.maturity_date < now])

    items: list[ExceptionItem] = []

    def add(kind, severity, fdr, inst, issue):
        items.append(ExceptionItem(kind=kind, severity=severity, fdr_id=fdr.id, fdr_number=fdr.fdr_number,
                                   institution_code=inst.code, institution_name=inst.name, status=fdr.status,
                                   issue=issue, link=link_for(user, fdr.id)))

    for fdr, inst in scan:
        if fdr.maturity_date < now and states.get(fdr.id) != release_state.APPROVED:
            late = (now - fdr.maturity_date).days
            add("OVERDUE_FDR", "HIGH", fdr, inst, f"{late} day{'s' if late != 1 else ''} past maturity with no approved release")
        missing = [name for name in SUBMISSION_REQUIRED_FIELDS if not getattr(fdr, name)]
        optional = [name for name in ("interest_rate_pct", "purpose") if getattr(fdr, name) in (None, "")]
        if missing or optional:
            parts = [*(FIELD_LABELS.get(n, n.replace("_", " ")).lower() for n in missing), *(FIELD_LABELS.get(n, n.replace("_", " ")).lower() for n in optional)]
            add("INCOMPLETE_RECORD", "MEDIUM" if missing else "LOW", fdr, inst, "Not provided: " + ", ".join(parts))
        f = findings.get(fdr.id)
        if f is not None:
            if f.documents_total == 0:
                add("MISSING_DOCUMENT", "MEDIUM", fdr, inst, "No supporting document has been uploaded")
            if can_see_results:
                if f.mismatch_fields:
                    add("FIELD_MISMATCH", "HIGH", fdr, inst, f"A document differs from the submitted record: {_label(f.mismatch_fields)}")
                if f.ambiguous_fields:
                    add("EXTRACTION_AMBIGUOUS", "MEDIUM", fdr, inst, f"Document values could not be read unambiguously: {_label(f.ambiguous_fields)}")
                if f.failed_documents:
                    add("EXTRACTION_FAILED", "MEDIUM", fdr, inst, f"{f.failed_documents} document{'s' if f.failed_documents != 1 else ''} could not be analysed")

    items.sort(key=lambda e: (_SEVERITY_ORDER[e.severity], e.fdr_number, e.kind))

    doc_note = None if tables else (
        "Unavailable until the document and extraction migrations are applied to this database."
        if can_see_documents else "Not available to your role."
    )
    result_note = None if can_see_results and tables else (
        "Unavailable until the document and extraction migrations are applied to this database." if can_see_results
        else "Extraction results are visible to AICTE reviewers and approvers only."
    )
    counts: dict[str, int] = {}
    for e in items:
        counts[e.kind] = counts.get(e.kind, 0) + 1
    kinds = [
        ExceptionKind(key="FIELD_MISMATCH", label="Document and record mismatch", total=counts.get("FIELD_MISMATCH", 0),
                      available=can_see_results and tables, note=result_note),
        ExceptionKind(key="EXTRACTION_AMBIGUOUS", label="Ambiguous document values", total=counts.get("EXTRACTION_AMBIGUOUS", 0),
                      available=can_see_results and tables, note=result_note),
        ExceptionKind(key="EXTRACTION_FAILED", label="Document processing failed", total=counts.get("EXTRACTION_FAILED", 0),
                      available=can_see_results and tables, note=result_note),
        ExceptionKind(key="MISSING_DOCUMENT", label="No supporting document", total=counts.get("MISSING_DOCUMENT", 0),
                      available=tables, note=doc_note),
        ExceptionKind(key="INCOMPLETE_RECORD", label="Incomplete record", total=counts.get("INCOMPLETE_RECORD", 0), available=True, note=None),
        ExceptionKind(key="OVERDUE_FDR", label="Past maturity, release not approved", total=counts.get("OVERDUE_FDR", 0), available=True, note=None),
    ]
    return ExceptionsOut(generated_on=now, scanned=len(scan), eligible_total=eligible_total, complete=complete, kinds=kinds, items=items)
