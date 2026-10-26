"""Read-only FDR endpoints (Stage 5b). No create/edit/submit/review/status-change routes exist yet.

Authorization is part of the SQL: every query starts from `fdr_visibility(<authenticated db user>)`,
which is applied before filtering, counting and pagination, so totals and pages can never include
records the caller may not read. Nothing in the request (query string, headers, body) can widen it.

Unreadable or missing records both return 404 "FDR not found". For institution users, a 404 on a
record outside their scope is also written to the audit log (`authz.scope_denied`), matching the
institution endpoints.
"""

import math
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, RequestContext, deny, get_request_context, require_authenticated
from app.db import get_db
from app.models import FDR, Bank, FdrStatus, Institution, Role
from app.models.fdr import normalize_fdr_number
from app.schemas.fdrs import BankOut, FdrList, FdrOut, InstitutionSummary
from app.security.scope import fdr_visibility

router = APIRouter(prefix="/fdrs", tags=["fdrs"])

MAX_PAGE = 100_000  # bounds OFFSET; deeper paging is meaningless at this scale
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25


def _escape_like(value: str) -> str:
    """Make %, _ and the escape character match literally in a LIKE pattern."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_authenticated),
) -> FdrList:
    response.headers["Cache-Control"] = "no-store"

    # Tenant/visibility scope first; every user-supplied filter can only narrow it further.
    conditions = [fdr_visibility(auth.user)]
    if status is not None:
        conditions.append(FDR.status == status.value)
    if bank_id is not None:
        conditions.append(FDR.bank_id == bank_id)
    if institution_id is not None and auth.user.role != Role.INSTITUTION_USER.value:
        conditions.append(FDR.institution_id == institution_id)
    if q is not None:
        try:
            normalized = normalize_fdr_number(q)
        except ValueError:
            raise HTTPException(status_code=422, detail="q must contain at least one non-whitespace character") from None
        conditions.append(FDR.fdr_number_normalized.like(_escape_like(normalized) + "%", escape="\\"))

    total = db.scalar(select(func.count()).select_from(FDR).where(*conditions)) or 0
    rows = db.execute(
        _joined()
        .where(*conditions)
        # Deterministic: newest first, with the unique id as tie-breaker so pages never overlap/skip.
        .order_by(FDR.created_at.desc(), FDR.id.desc())
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
