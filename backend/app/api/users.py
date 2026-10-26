"""Administrator-managed user accounts (ADMIN only). There is no self-registration anywhere.

Last-active-ADMIN invariant
---------------------------
PATCH locks, in one `SELECT ... FOR UPDATE ORDER BY id`, the target user, the acting admin and EVERY
active admin. Concurrent PATCHes therefore queue on the same rows in the same order (no deadlock),
and the count of remaining active admins is computed against rows nobody else can change. A request
that waited for the lock also re-checks that the actor is still an active ADMIN, so an admin demoted
by a concurrent request cannot finish an update on stale authority. Every change and its audit
events are written in one transaction: either all of it commits or none of it does.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, RequestContext, deny, get_request_context, require_roles
from app.audit import AuditOutcome, write_audit_event
from app.config import Settings, get_settings
from app.db import get_db
from app.models import Institution, Role, User
from app.schemas.users import UserAdminOut, UserCreate, UserCreated, UserList, UserUpdate
from app.security.cookies import clear_session_cookie
from app.security.emails import InvalidEmail, normalize_email
from app.security.lockout import is_locked
from app.security.passwords import generate_temporary_password, hash_password
from app.security.sessions import revoke_user_sessions
from app.timeutil import utcnow

router = APIRouter(prefix="/users", tags=["users"])

admin_only = require_roles(Role.ADMIN)
ADMIN = Role.ADMIN.value
INSTITUTION_USER = Role.INSTITUTION_USER.value


def _view(user: User, now: datetime | None = None) -> UserAdminOut:
    return UserAdminOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        institution_id=user.institution_id,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        is_locked=is_locked(user, now or utcnow()),
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


def _audit(db: Session, ctx: RequestContext, auth: AuthContext, action: str, target: User, **details):
    # Details are restricted to ids, roles and flags: no emails, names, passwords or hashes.
    return write_audit_event(
        db,
        action=action,
        outcome=AuditOutcome.SUCCESS,
        actor_user_id=auth.user.id,
        actor_role=auth.user.role,
        entity_type="user",
        entity_id=target.id,
        ip_address=ctx.ip_address,
        request_id=ctx.request_id,
        details=details or None,
    )


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status_code=422, detail=message)


@router.get("", response_model=UserList)
def list_users(
    role: Role | None = None,
    is_active: bool | None = None,
    institution_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _auth: AuthContext = Depends(admin_only),
) -> UserList:
    stmt = select(User)
    if role is not None:
        stmt = stmt.where(User.role == role.value)
    if is_active is not None:
        stmt = stmt.where(User.is_active.is_(is_active))
    if institution_id is not None:
        stmt = stmt.where(User.institution_id == institution_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(User.email).limit(limit).offset(offset)).all()
    now = utcnow()
    return UserList(items=[_view(u, now) for u in rows], total=total, limit=limit, offset=offset)


@router.post("", response_model=UserCreated, status_code=201)
def create_user(
    body: UserCreate,
    response: Response,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(admin_only),
) -> UserCreated:
    response.headers["Cache-Control"] = "no-store"
    try:
        email = normalize_email(body.email)
    except InvalidEmail:
        raise _unprocessable("Invalid email address") from None
    if body.institution_id is not None and db.get(Institution, body.institution_id) is None:
        raise _unprocessable("Unknown institution")
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(status_code=409, detail="A user with this email already exists")

    temporary_password = generate_temporary_password()
    user = User(
        email=email,
        full_name=body.full_name,
        password_hash=hash_password(temporary_password),
        role=body.role.value,
        institution_id=body.institution_id,
        is_active=True,
        must_change_password=True,
    )
    db.add(user)
    try:
        db.flush()  # surfaces a concurrent duplicate-email insert as IntegrityError
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A user with this email already exists") from None

    details: dict = {"new_role": user.role, "new_is_active": True}
    if user.institution_id is not None:
        details["new_institution_id"] = str(user.institution_id)
    _audit(db, ctx, auth, "user.create", user, **details)
    db.commit()
    return UserCreated(user=_view(user), temporary_password=temporary_password)


@router.get("/{user_id}", response_model=UserAdminOut)
def get_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    _auth: AuthContext = Depends(admin_only),
) -> UserAdminOut:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return _view(user)


@router.patch("/{user_id}", response_model=UserAdminOut)
def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(admin_only),
) -> UserAdminOut:
    provided = body.model_fields_set

    # --- lock target + actor + every active admin, in a fixed order -------------------------
    locked = db.scalars(
        select(User)
        .where(
            or_(
                User.id.in_([user_id, auth.user.id]),
                and_(User.role == ADMIN, User.is_active.is_(True)),
            )
        )
        .order_by(User.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    by_id = {u.id: u for u in locked}

    actor = by_id.get(auth.user.id)
    if actor is None or actor.role != ADMIN or not actor.is_active or actor.must_change_password:
        # Authority was removed by a concurrent request while we waited for the lock.
        raise deny(db, request, auth, ctx, reason="role_not_permitted", required_roles=[ADMIN])
    target = by_id.get(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    # --- resolve the requested end state and validate it -------------------------------------
    new_role = body.role.value if "role" in provided else target.role
    if "institution_id" in provided:
        new_institution_id = body.institution_id
    elif new_role != INSTITUTION_USER:
        new_institution_id = None  # leaving INSTITUTION_USER clears the assignment
    else:
        new_institution_id = target.institution_id
    new_active = body.is_active if "is_active" in provided else target.is_active
    new_name = body.full_name.strip() if "full_name" in provided else target.full_name

    if new_role == INSTITUTION_USER and new_institution_id is None:
        raise _unprocessable("institution_id is required for INSTITUTION_USER")
    if new_role != INSTITUTION_USER and new_institution_id is not None:
        raise _unprocessable("institution_id is only allowed for INSTITUTION_USER")
    if new_institution_id is not None and new_institution_id != target.institution_id:
        if db.get(Institution, new_institution_id) is None:
            raise _unprocessable("Unknown institution")

    role_changed = new_role != target.role
    institution_changed = new_institution_id != target.institution_id
    active_changed = new_active != target.is_active
    name_changed = new_name != target.full_name
    if not (role_changed or institution_changed or active_changed or name_changed):
        return _view(target)  # nothing to do; nothing to audit

    # --- last-active-admin invariant (all active admins are locked, so this count is stable) --
    remaining_admins = sum(
        1 for u in locked if u.id != target.id and u.role == ADMIN and u.is_active
    ) + (1 if new_role == ADMIN and new_active else 0)
    if remaining_admins < 1:
        raise deny(
            db, request, auth, ctx,
            status_code=409, message="This change would leave no active administrator",
            reason="last_admin_protection",
        )

    # --- apply + audit, all in this one transaction ------------------------------------------
    old_role, old_institution_id = target.role, target.institution_id
    target.role, target.institution_id, target.is_active, target.full_name = (
        new_role, new_institution_id, new_active, new_name,
    )

    if role_changed:
        _audit(db, ctx, auth, "user.role_change", target, old_role=old_role, new_role=new_role)
    if institution_changed:
        details = {}
        if old_institution_id is not None:
            details["old_institution_id"] = str(old_institution_id)
        if new_institution_id is not None:
            details["new_institution_id"] = str(new_institution_id)
        _audit(db, ctx, auth, "user.institution_change", target, **details)
    deactivated = active_changed and not new_active
    if active_changed:
        if deactivated:
            revoked = revoke_user_sessions(db, target.id)  # sign them out everywhere, now
            _audit(db, ctx, auth, "user.deactivate", target, old_is_active=True, new_is_active=False,
                   revoked_count=revoked)
        else:
            _audit(db, ctx, auth, "user.activate", target, old_is_active=False, new_is_active=True)
    if name_changed:
        _audit(db, ctx, auth, "user.update", target, changed_fields=["full_name"])

    try:
        db.commit()
    except IntegrityError:  # a database constraint we did not anticipate: fail closed, change nothing
        db.rollback()
        raise HTTPException(status_code=409, detail="Update conflicts with existing data") from None

    if deactivated and target.id == auth.user.id:
        clear_session_cookie(response, settings)
    return _view(target)
