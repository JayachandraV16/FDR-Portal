"""Authentication endpoints: login, logout, current user, change password.

There is deliberately no registration endpoint; accounts are created by an administrator
(Stage 4) or the development bootstrap (Stage 7).

Transaction rules (they matter for correctness):
  * The user row is locked with SELECT ... FOR UPDATE before the failed-login counter is read, so
    concurrent attempts are serialised and cannot slip past the lockout threshold.
  * User rows are always locked BEFORE an audit event is written (the audit writer takes a global
    advisory lock), giving one lock order everywhere and ruling out deadlocks.
  * Failure paths commit (counter + audit) first and only then raise, so a rejected request still
    leaves its audit trail.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    AuthContext,
    NotAuthenticated,
    RequestContext,
    authenticate,
    enforce_unsafe_request_checks,
    get_auth_context,
    get_request_context,
    has_trusted_origin,
)
from app.audit import AuditOutcome, write_audit_event
from app.config import Settings, get_settings
from app.db import get_db
from app.models import User
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    MessageResponse,
    SessionResponse,
    UserOut,
)
from app.security.cookies import SESSION_COOKIE_NAME, clear_session_cookie, set_session_cookie
from app.security.csrf import generate_csrf_token
from app.security.emails import InvalidEmail, normalize_email
from app.security.lockout import is_locked, register_failure, register_success
from app.security.passwords import (
    PasswordPolicyError,
    hash_password,
    validate_password_policy,
    verify_password,
)
from app.security.sessions import (
    SessionStatus,
    create_session,
    find_session,
    revoke_session,
    revoke_user_sessions,
)
from app.timeutil import utcnow

router = APIRouter(prefix="/auth", tags=["auth"])

INVALID_CREDENTIALS = "Invalid credentials"


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _audit(db: Session, ctx: RequestContext, action: str, outcome: AuditOutcome, user: User | None, **details):
    return write_audit_event(
        db,
        action=action,
        outcome=outcome,
        actor_user_id=user.id if user else None,
        actor_role=user.role if user else None,
        entity_type="user" if user else None,
        entity_id=user.id if user else None,
        ip_address=ctx.ip_address,
        request_id=ctx.request_id,
        details=details or None,
    )


def _login_failed(db: Session, ctx: RequestContext, user: User | None, reason: str, **extra) -> HTTPException:
    """Audit + commit a failed login, then hand back the single generic error to raise."""
    _audit(db, ctx, "auth.login", AuditOutcome.FAILURE if reason != "account_locked" else AuditOutcome.DENIED,
           user, reason=reason, **extra)
    db.commit()
    return HTTPException(status_code=401, detail=INVALID_CREDENTIALS)


@router.post("/login", response_model=SessionResponse)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: RequestContext = Depends(get_request_context),
) -> SessionResponse:
    _no_store(response)
    # Login CSRF defence. Not audited: there is no authenticated actor, and anonymous callers must
    # not be able to write to the audit trail.
    if not has_trusted_origin(request, settings):
        raise HTTPException(status_code=403, detail="Untrusted request origin")

    now = utcnow()
    try:
        email: str | None = normalize_email(body.email)
    except InvalidEmail:
        email = None

    user: User | None = None
    if email is not None:
        user = db.scalars(select(User).where(User.email == email).with_for_update()).first()

    # Exactly one Argon2 verification on every path (a dummy one when the account is unknown).
    check = verify_password(user.password_hash if user else None, body.password)

    if user is None:
        raise _login_failed(db, ctx, None, "unknown_account")
    if not user.is_active:
        raise _login_failed(db, ctx, user, "account_inactive")
    if is_locked(user, now):
        raise _login_failed(db, ctx, user, "account_locked")
    if not check.valid:
        locked, attempts = register_failure(user, settings, now)
        _audit(db, ctx, "auth.login", AuditOutcome.FAILURE, user, reason="bad_password", failed_login_count=attempts)
        if locked:
            _audit(db, ctx, "auth.lockout", AuditOutcome.DENIED, user, reason="too_many_failures", locked=True)
        db.commit()
        raise HTTPException(status_code=401, detail=INVALID_CREDENTIALS)

    # --- success ---
    register_success(user, now)
    if check.new_hash:
        user.password_hash = check.new_hash  # parameters were outdated; upgrade transparently

    # Any session the browser presented is retired so a pre-login cookie can never be "upgraded".
    old, _status = find_session(db, request.cookies.get(SESSION_COOKIE_NAME), settings, now=now)
    if old is not None:
        revoke_session(old, now)

    issued = create_session(
        db, user.id, settings, now=now, ip_address=ctx.ip_address, user_agent=ctx.user_agent
    )
    _audit(db, ctx, "auth.login", AuditOutcome.SUCCESS, user)
    db.commit()

    set_session_cookie(response, issued.raw_token, settings)
    return SessionResponse(
        user=UserOut.model_validate(user),
        csrf_token=generate_csrf_token(issued.session.id, settings.secret_key.get_secret_value()),
    )


@router.get("/me", response_model=SessionResponse)
def me(response: Response, auth: AuthContext = Depends(get_auth_context)) -> SessionResponse:
    _no_store(response)
    return SessionResponse(user=UserOut.model_validate(auth.user), csrf_token=auth.csrf_token)


@router.post("/logout", response_model=MessageResponse)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: RequestContext = Depends(get_request_context),
) -> MessageResponse:
    _no_store(response)
    try:
        auth = authenticate(db, request, settings)
    except NotAuthenticated:
        # Nothing valid to revoke; stay idempotent and just make sure the cookie is gone.
        clear_session_cookie(response, settings)
        return MessageResponse(detail="Signed out")
    enforce_unsafe_request_checks(db, request, auth, settings, ctx)  # CSRF + origin
    revoke_session(auth.session)
    _audit(db, ctx, "auth.logout", AuditOutcome.SUCCESS, auth.user)
    db.commit()
    clear_session_cookie(response, settings)
    return MessageResponse(detail="Signed out")


@router.post("/change-password", response_model=MessageResponse)
def change_password(
    body: ChangePasswordRequest,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(get_auth_context),  # session + origin + CSRF already enforced
) -> MessageResponse:
    _no_store(response)
    now = utcnow()
    # Re-load with a row lock so the failure counter is race-free here too.
    user = db.scalars(
        select(User).where(User.id == auth.user.id).with_for_update().execution_options(populate_existing=True)
    ).one()

    check = verify_password(user.password_hash, body.current_password)
    if not check.valid:
        # A stolen session must not be a free oracle for guessing the current password.
        locked, attempts = register_failure(user, settings, now)
        _audit(db, ctx, "auth.password_change", AuditOutcome.FAILURE, user,
               reason="bad_current_password", failed_login_count=attempts)
        if locked:
            revoked = revoke_user_sessions(db, user.id, now=now)
            _audit(db, ctx, "auth.lockout", AuditOutcome.DENIED, user,
                   reason="too_many_failures", locked=True, revoked_count=revoked)
        db.commit()
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    try:
        validate_password_policy(body.new_password, user.email)
    except PasswordPolicyError as exc:
        _audit(db, ctx, "auth.password_change", AuditOutcome.FAILURE, user, reason="policy_violation")
        db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if body.new_password == body.current_password:
        _audit(db, ctx, "auth.password_change", AuditOutcome.FAILURE, user, reason="same_password")
        db.commit()
        raise HTTPException(status_code=400, detail="New password must differ from the current password")

    user.password_hash = hash_password(body.new_password)
    user.password_changed_at = now
    user.must_change_password = False
    user.failed_login_count = 0
    revoked = revoke_user_sessions(db, user.id, now=now)  # includes the current session
    _audit(db, ctx, "auth.password_change", AuditOutcome.SUCCESS, user, revoked_count=revoked)
    db.commit()

    clear_session_cookie(response, settings)
    return MessageResponse(detail="Password changed. Please sign in again.")
