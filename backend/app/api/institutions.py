"""Read-only institution endpoints (create/edit arrive in a later checkpoint).

Visibility comes exclusively from app.security.scope.scoped_institutions(<authenticated db user>).
These routes accept no institution identifier other than the path ID of the record being fetched,
and that ID is always checked against the scoped query; it never grants access by itself.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, RequestContext, deny, get_request_context, require_authenticated
from app.db import get_db
from app.schemas.institutions import InstitutionList, InstitutionOut
from app.security.scope import ALL_INSTITUTIONS_ROLES, scoped_institutions
from app.models import Institution

router = APIRouter(prefix="/institutions", tags=["institutions"])


@router.get("", response_model=InstitutionList)
def list_institutions(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_authenticated),
) -> InstitutionList:
    base = scoped_institutions(auth.user)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(base.order_by(Institution.code).limit(limit).offset(offset)).all()
    return InstitutionList(
        items=[InstitutionOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.get("/{institution_id}", response_model=InstitutionOut)
def get_institution(
    institution_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(require_authenticated),
) -> InstitutionOut:
    institution = db.scalars(scoped_institutions(auth.user).where(Institution.id == institution_id)).first()
    if institution is None:
        if auth.user.role in ALL_INSTITUTIONS_ROLES:
            raise HTTPException(status_code=404, detail="Institution not found")  # genuinely absent
        # Same 404 whether the record is missing or merely out of scope, so existence is not
        # confirmed. The attempt is audited for the actor (the audit trail is not user-visible).
        raise deny(
            db, request, auth, ctx,
            action="authz.scope_denied", status_code=404, message="Institution not found",
            target_institution_id=str(institution_id),
        )
    return InstitutionOut.model_validate(institution)
