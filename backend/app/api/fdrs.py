"""FDR endpoints: role-scoped reads, plus the institution-side draft workflow (create, edit, submit).

Reads. Authorization is part of the SQL: every query starts from `fdr_visibility(<authenticated db
user>)`, which is applied before filtering, counting and pagination, so totals and pages can never
include records the caller may not read. Nothing in the request can widen it. Unreadable or missing
records both return 404 "FDR not found"; for institution users a 404 on a record outside their scope
is also audited (`authz.scope_denied`).

Writes (INSTITUTION_USER only; session, Origin and CSRF are enforced by `require_roles` on every
unsafe method):
  * POST   /fdrs              creates a DRAFT in the caller's own institution. The institution, status,
                              owner and row_version are decided here; the request cannot set them.
  * PATCH  /fdrs/{id}         edits a DRAFT, or a record a reviewer returned (CORRECTION_REQUESTED).
                              Needs the current `row_version` (409 if it moved).
  * POST   /fdrs/{id}/submit  DRAFT -> SUBMITTED, or CORRECTION_REQUESTED -> SUBMITTED (resubmission),
                              freezing a new revision snapshot each time; earlier ones are kept.
Reads of a record's history (GET /fdrs/{id}/history) follow the same visibility as the record itself.
The review decisions live in app/api/review.py. The only transitions reachable through this module
are the institution's DRAFT/CORRECTION_REQUESTED -> SUBMITTED. There is no endpoint that accepts a
status from the client. Every write locks the FDR row (`SELECT ... FOR UPDATE`, scoped to
the caller's institution in the same query), re-checks status and version under the lock, and
changes the row, its history/revision rows and its audit event in one transaction. Row locks are
taken BEFORE the audit event (which takes the global audit lock): the same order as everywhere else.
"""

import math
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, RequestContext, deny, get_request_context, require_authenticated, require_roles
from app.audit import AuditOutcome, write_audit_event
from app.db import get_db
from app.models import FDR, Bank, FdrStatus, FDRRevision, FDRStatusHistory, Institution, Role
from app.models.fdr import fdr_snapshot, normalize_fdr_number
from app.schemas.fdrs import (
    SUBMISSION_REQUIRED_FIELDS,
    BankOut,
    FdrCreate,
    FdrHistory,
    FdrList,
    FdrOut,
    FdrSubmit,
    FdrUpdate,
    HistoryEntryOut,
    InstitutionSummary,
    RevisionInfo,
)
from app.security.scope import fdr_visibility
from app.timeutil import utcnow

router = APIRouter(prefix="/fdrs", tags=["fdrs"])
institution_only = require_roles(Role.INSTITUTION_USER)

MAX_PAGE = 100_000  # bounds OFFSET; deeper paging is meaningless at this scale
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25


def _escape_like(value: str) -> str:
    """Make %, _ and the escape character match literally in a LIKE pattern."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def scoped_conditions(user, *, bank_id: uuid.UUID | None, institution_id: uuid.UUID | None, q: str | None) -> list:
    """WHERE conditions shared by every FDR listing. Tenant/visibility scope comes first; each
    user-supplied filter can only narrow it further."""
    conditions = [fdr_visibility(user)]
    if bank_id is not None:
        conditions.append(FDR.bank_id == bank_id)
    if institution_id is not None and user.role != Role.INSTITUTION_USER.value:
        conditions.append(FDR.institution_id == institution_id)
    if q is not None:
        try:
            normalized = normalize_fdr_number(q)
        except ValueError:
            raise HTTPException(status_code=422, detail="q must contain at least one non-whitespace character") from None
        conditions.append(FDR.fdr_number_normalized.like(_escape_like(normalized) + "%", escape="\\"))
    return conditions


def _to_out(fdr: FDR, bank: Bank, institution: Institution) -> FdrOut:
    return FdrOut(
        id=fdr.id,
        institution=InstitutionSummary(id=institution.id, code=institution.code, name=institution.name),
        bank=BankOut.model_validate(bank),
        fdr_number=fdr.fdr_number,
        branch_name=fdr.branch_name,
        depositor_name=fdr.depositor_name,
        purpose=fdr.purpose,
        principal_amount=fdr.principal_amount,
        interest_rate_pct=fdr.interest_rate_pct,
        issue_date=fdr.issue_date,
        maturity_date=fdr.maturity_date,
        status=fdr.status,
        row_version=fdr.row_version,
        created_at=fdr.created_at,
        updated_at=fdr.updated_at,
        submitted_at=fdr.submitted_at,
        decided_at=fdr.decided_at,
    )


_SORT_COLUMNS = {
    "created": FDR.created_at, "number": FDR.fdr_number_normalized, "principal": FDR.principal_amount,
    "maturity": FDR.maturity_date, "issue": FDR.issue_date, "status": FDR.status,
}


def _ordering(sort: str, order: str | None):
    """ORDER BY terms from a whitelisted column name (never from request text) plus the id tie-breaker.
    'created' defaults to newest first; every other column defaults to ascending."""
    column = _SORT_COLUMNS[sort]
    descending = (order == "desc") if order else sort == "created"
    return (column.desc(), FDR.id.desc()) if descending else (column.asc(), FDR.id.asc())


def _joined():
    return select(FDR, Bank, Institution).join(Bank, Bank.id == FDR.bank_id).join(
        Institution, Institution.id == FDR.institution_id
    )


@router.get("", response_model=FdrList)
def list_fdrs(
    response: Response,
    page: int = Query(1, ge=1, le=MAX_PAGE),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    status: FdrStatus | None = Query(None, description="Only records in this status"),
    bank_id: uuid.UUID | None = Query(None),
    institution_id: uuid.UUID | None = Query(
        None, description="Cross-institution roles only. Ignored for institution users, who are always scoped."
    ),
    q: str | None = Query(
        None, min_length=1, max_length=64,
        description="FDR number prefix; case, spaces and tabs are ignored (same normalisation as duplicate detection)",
    ),
    sort: Literal["created", "number", "principal", "maturity", "issue", "status"] = Query(
        "created", description="Column to order by (default: newest first)"
    ),
    order: Literal["asc", "desc"] | None = Query(None, description="Direction; default is newest/largest first for 'created'"),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_authenticated),
) -> FdrList:
    response.headers["Cache-Control"] = "no-store"

    conditions = scoped_conditions(auth.user, bank_id=bank_id, institution_id=institution_id, q=q)
    if status is not None:
        conditions.append(FDR.status == status.value)

    total = db.scalar(select(func.count()).select_from(FDR).where(*conditions)) or 0
    rows = db.execute(
        _joined()
        .where(*conditions)
        # Deterministic whatever the column: the unique id is the tie-breaker, so pages never overlap or skip.
        .order_by(*_ordering(sort, order))
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()
    return FdrList(
        items=[_to_out(f, b, i) for f, b, i in rows],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=math.ceil(total / page_size),
    )


@router.get("/{fdr_id}", response_model=FdrOut)
def get_fdr(
    fdr_id: uuid.UUID,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(require_authenticated),
) -> FdrOut:
    response.headers["Cache-Control"] = "no-store"
    row = db.execute(_joined().where(FDR.id == fdr_id, fdr_visibility(auth.user))).first()
    if row is None:
        if auth.user.role == Role.INSTITUTION_USER.value:
            # Same body as a nonexistent ID, so existence is not confirmed; the attempt is audited.
            raise deny(
                db, request, auth, ctx,
                action="authz.scope_denied", status_code=404, message="FDR not found",
                target_fdr_id=str(fdr_id),
            )
        raise HTTPException(status_code=404, detail="FDR not found")
    return _to_out(*row)


@router.get("/{fdr_id}/history", response_model=FdrHistory)
def fdr_history(
    fdr_id: uuid.UUID,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(require_authenticated),
) -> FdrHistory:
    """Status history (with review comments) and revision numbers for a record the caller may read.
    Visibility is exactly that of GET /fdrs/{id}: anyone who can see the FDR can see how it got here,
    and nobody else learns that it exists."""
    response.headers["Cache-Control"] = "no-store"
    if db.scalar(select(FDR.id).where(FDR.id == fdr_id, fdr_visibility(auth.user))) is None:
        if auth.user.role == Role.INSTITUTION_USER.value:
            raise deny(
                db, request, auth, ctx,
                action="authz.scope_denied", status_code=404, message="FDR not found",
                target_fdr_id=str(fdr_id),
            )
        raise HTTPException(status_code=404, detail="FDR not found")
    history = db.scalars(
        select(FDRStatusHistory).where(FDRStatusHistory.fdr_id == fdr_id).order_by(FDRStatusHistory.occurred_at, FDRStatusHistory.id)
    ).all()
    revisions = db.scalars(
        select(FDRRevision).where(FDRRevision.fdr_id == fdr_id).order_by(FDRRevision.revision_no)
    ).all()
    return FdrHistory(
        history=[HistoryEntryOut.model_validate(h, from_attributes=True) for h in history],
        revisions=[RevisionInfo(revision_no=r.revision_no, submitted_at=r.submitted_at) for r in revisions],
    )


# ---- institution-side draft workflow ---------------------------------------------------------

# The institution may change a record only while it is theirs to work on: a draft, or one a reviewer
# has sent back. Every other status belongs to the review process.
EDITABLE_STATUSES = (FdrStatus.DRAFT, FdrStatus.CORRECTION_REQUESTED)

EDITABLE_FIELDS = (
    "bank_id", "fdr_number", "branch_name", "depositor_name", "purpose",
    "principal_amount", "interest_rate_pct", "issue_date", "maturity_date",
)


def _audit(db: Session, ctx: RequestContext, auth: AuthContext, action: str, fdr: FDR, **details):
    # Details are restricted to field NAMES; no values, amounts, numbers or names of anything.
    return write_audit_event(
        db,
        action=action,
        outcome=AuditOutcome.SUCCESS,
        actor_user_id=auth.user.id,
        actor_role=auth.user.role,
        entity_type="fdr",
        entity_id=fdr.id,
        ip_address=ctx.ip_address,
        request_id=ctx.request_id,
        details=details or None,
    )


def _response(db: Session, fdr_id: uuid.UUID) -> FdrOut:
    row = db.execute(_joined().where(FDR.id == fdr_id).execution_options(populate_existing=True)).one()
    return _to_out(*row)


def locked_visible_fdr(
    db: Session, request: Request, auth: AuthContext, ctx: RequestContext, fdr_id: uuid.UUID
) -> FDR:
    """Lock the FDR row for update, found through the caller's visibility scope in the same query.
    A record in another institution is indistinguishable from a missing one (404) and is audited."""
    fdr = db.scalars(
        select(FDR)
        .where(FDR.id == fdr_id, fdr_visibility(auth.user))
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if fdr is None:
        raise deny(
            db, request, auth, ctx,
            action="authz.scope_denied", status_code=404, message="FDR not found",
            target_fdr_id=str(fdr_id),
        )
    return fdr


def require_status_and_version(
    db: Session, request: Request, auth: AuthContext, ctx: RequestContext, fdr: FDR,
    *, action: str, row_version: int, allowed: tuple[FdrStatus, ...], wrong_status_message: str,
) -> None:
    """Under the row lock: the record must be in one of `allowed` and the caller's version current.
    Both failures are 409s and are audited as denials."""
    if fdr.status not in {s.value for s in allowed}:
        raise deny(
            db, request, auth, ctx,
            action=action, status_code=409, message=wrong_status_message,
            reason="invalid_status", target_fdr_id=str(fdr.id),
        )
    if row_version != fdr.row_version:
        raise deny(
            db, request, auth, ctx,
            action=action, status_code=409,
            message="This FDR was changed since you loaded it. Reload it and try again.",
            reason="stale_row_version", target_fdr_id=str(fdr.id),
        )


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status_code=422, detail=message)


def add_draft_fdr(db: Session, ctx: RequestContext, auth: AuthContext, body: FdrCreate, institution_id: uuid.UUID, normalized: str) -> FDR:
    """The one place a new FDR draft is built: the record, its first status-history entry and its audit event, in the
    caller's transaction (the caller commits). The Create FDR endpoint and the bulk importer both go through here, so
    an imported row can only ever become what a hand-entered one becomes: a DRAFT owned by the caller's institution."""
    fdr = FDR(
        institution_id=institution_id,  # always the caller's own; never from the request
        bank_id=body.bank_id,
        fdr_number=body.fdr_number,
        fdr_number_normalized=normalized,
        branch_name=body.branch_name,
        depositor_name=body.depositor_name,
        purpose=body.purpose,
        principal_amount=body.principal_amount,
        interest_rate_pct=body.interest_rate_pct,
        issue_date=body.issue_date,
        maturity_date=body.maturity_date,
        status=FdrStatus.DRAFT.value,
        row_version=1,
        created_by=auth.user.id,
    )
    db.add(fdr)
    db.flush()
    db.add(FDRStatusHistory(
        fdr_id=fdr.id, from_status=None, to_status=FdrStatus.DRAFT.value,
        actor_user_id=auth.user.id, actor_role=auth.user.role,
    ))
    _audit(db, ctx, auth, "fdr.create", fdr, changed_fields=sorted(body.model_fields_set))
    return fdr


@router.post("", response_model=FdrOut, status_code=201)
def create_fdr(
    body: FdrCreate,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(institution_only),
) -> FdrOut:
    response.headers["Cache-Control"] = "no-store"
    institution_id = auth.user.institution_id
    if institution_id is None:  # inconsistent account: fail closed
        raise deny(db, request, auth, ctx, reason="no_institution")
    if db.get(Bank, body.bank_id) is None:
        raise _unprocessable("Unknown bank")
    try:
        normalized = normalize_fdr_number(body.fdr_number)
    except ValueError:
        raise _unprocessable("FDR number is required") from None

    try:
        fdr = add_draft_fdr(db, ctx, auth, body, institution_id, normalized)
        db.commit()
    except IntegrityError:  # a database constraint we did not anticipate: fail closed
        db.rollback()
        raise HTTPException(status_code=409, detail="The FDR conflicts with existing data") from None
    return _response(db, fdr.id)


@router.patch("/{fdr_id}", response_model=FdrOut)
def update_fdr(
    fdr_id: uuid.UUID,
    body: FdrUpdate,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(institution_only),
) -> FdrOut:
    response.headers["Cache-Control"] = "no-store"
    fdr = locked_visible_fdr(db, request, auth, ctx, fdr_id)
    require_status_and_version(
        db, request, auth, ctx, fdr,
        action="fdr.update", row_version=body.row_version, allowed=EDITABLE_STATUSES,
        wrong_status_message="Only draft FDRs, or FDRs returned for correction, can be edited",
    )

    provided = body.model_fields_set - {"row_version"}
    new = {name: getattr(body, name) for name in EDITABLE_FIELDS if name in provided}
    if "bank_id" in new and new["bank_id"] != fdr.bank_id and db.get(Bank, new["bank_id"]) is None:
        raise _unprocessable("Unknown bank")
    issue = new.get("issue_date", fdr.issue_date)
    maturity = new.get("maturity_date", fdr.maturity_date)
    if maturity <= issue:
        raise _unprocessable("maturity_date must be after issue_date")
    if "fdr_number" in new:
        try:
            new["fdr_number_normalized"] = normalize_fdr_number(new["fdr_number"])
        except ValueError:
            raise _unprocessable("FDR number is required") from None

    changed = sorted(name for name in EDITABLE_FIELDS if name in new and new[name] != getattr(fdr, name))
    if not changed:
        return _response(db, fdr.id)  # nothing to do: no version bump, nothing to audit

    for name in changed:
        setattr(fdr, name, new[name])
    if "fdr_number" in changed:
        fdr.fdr_number_normalized = new["fdr_number_normalized"]
    fdr.row_version += 1
    try:
        _audit(db, ctx, auth, "fdr.update", fdr, changed_fields=changed)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="The update conflicts with existing data") from None
    return _response(db, fdr.id)


@router.post("/{fdr_id}/submit", response_model=FdrOut)
def submit_fdr(
    fdr_id: uuid.UUID,
    body: FdrSubmit,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(institution_only),
) -> FdrOut:
    """DRAFT -> SUBMITTED, or CORRECTION_REQUESTED -> SUBMITTED (resubmission after corrections). These
    are the only status changes available to the institution, and neither can be repeated: after the
    first success the row is SUBMITTED, so a second or concurrent call waits for the row lock and then
    gets 409. A resubmission adds a new revision; earlier revisions and history are never touched."""
    response.headers["Cache-Control"] = "no-store"
    fdr = locked_visible_fdr(db, request, auth, ctx, fdr_id)
    previous_status = fdr.status
    action = "fdr.resubmit" if previous_status == FdrStatus.CORRECTION_REQUESTED.value else "fdr.submit"
    require_status_and_version(
        db, request, auth, ctx, fdr,
        action=action, row_version=body.row_version, allowed=EDITABLE_STATUSES,
        wrong_status_message="Only draft FDRs, or FDRs returned for correction, can be submitted",
    )
    missing = [name for name in SUBMISSION_REQUIRED_FIELDS if not getattr(fdr, name)]
    if missing:
        raise _unprocessable("Required before submission: " + ", ".join(missing))

    now = utcnow()
    fdr.status = FdrStatus.SUBMITTED.value
    fdr.submitted_by = auth.user.id
    fdr.submitted_at = now
    fdr.row_version += 1
    try:
        # Flush the status change first: this is where the partial unique index (one live record per
        # number per bank) can reject it, and later queries would otherwise trigger it by autoflush.
        db.flush()
        revision_no = (db.scalar(select(func.max(FDRRevision.revision_no)).where(FDRRevision.fdr_id == fdr.id)) or 0) + 1
        db.add(FDRRevision(
            fdr_id=fdr.id, revision_no=revision_no, snapshot=fdr_snapshot(fdr), submitted_by=auth.user.id,
        ))
        db.add(FDRStatusHistory(
            fdr_id=fdr.id, from_status=previous_status, to_status=FdrStatus.SUBMITTED.value,
            actor_user_id=auth.user.id, actor_role=auth.user.role,
        ))
        _audit(db, ctx, auth, action, fdr, changed_fields=["status"])
        db.commit()
    except IntegrityError:
        # The partial unique index: another live record already uses this number at this bank.
        db.rollback()
        raise deny(
            db, request, auth, ctx,
            action=action, status_code=409,
            message="An active FDR with this number already exists for this bank",
            reason="duplicate_active_fdr_number", target_fdr_id=str(fdr_id),
        ) from None
    return _response(db, fdr.id)
