"""Request-level authentication helpers shared by all cookie-authenticated routes.

Order of checks for an unsafe (non-GET/HEAD/OPTIONS) authenticated request:
session -> trusted Origin/Referer -> CSRF token. Rejections of a *known* session are audited and
committed before the error is raised; rejections with no valid session are not audited, so an
anonymous caller cannot flood the audit trail.
"""

import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.audit import AuditOutcome, write_audit_event
from app.config import Settings, get_settings
from app.db import get_db
from app.models import AuthSession, Role, User
from app.security.csrf import generate_csrf_token, validate_csrf_token
from app.security.origin import is_trusted_origin
from app.security.sessions import (
    SessionStatus,
    find_session,
    revoke_session,
    touch_session,
)
from app.security.cookies import SESSION_COOKIE_NAME

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_HEADER = "X-CSRF-Token"


class NotAuthenticated(Exception):
    """Raised when there is no valid session; the app handler returns 401 and clears the cookie."""

    def __init__(self, settings: Settings):
        self.settings = settings


@dataclass(frozen=True)
class RequestContext:
    ip_address: str | None
    user_agent: str | None
    request_id: str


@dataclass(frozen=True)
class AuthContext:
    user: User
    session: AuthSession
    csrf_token: str


def get_request_context(request: Request) -> RequestContext:
    # request.client is the direct peer. X-Forwarded-For is NOT trusted (no proxy config yet).
    request_id = getattr(request.state, "request_id", None)
    if request_id is None:
        request_id = request.state.request_id = uuid.uuid4().hex
    return RequestContext(
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        request_id=request_id,
    )


def has_trusted_origin(request: Request, settings: Settings) -> bool:
    return is_trusted_origin(
        request.headers.get("origin"), request.headers.get("referer"), settings.cors_origins
    )


def authenticate(db: Session, request: Request, settings: Settings) -> AuthContext:
    """Validate the session cookie and load the user. Does not apply CSRF/origin checks."""
    session, status = find_session(db, request.cookies.get(SESSION_COOKIE_NAME), settings)
    if session is None or status is not SessionStatus.VALID:
        raise NotAuthenticated(settings)
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        # Deactivation takes effect immediately: kill the session, not just this request.
        revoke_session(session)
        db.commit()
        raise NotAuthenticated(settings)
    if touch_session(session):
        db.commit()
    return AuthContext(
        user=user,
        session=session,
        csrf_token=generate_csrf_token(session.id, settings.secret_key.get_secret_value()),
    )


def _route_template(request: Request) -> str:
    # The matched route's own .path omits include_router prefixes, so rebuild the template from the
    # real path by swapping path-parameter values for their names (keeps record IDs out of the audit).
    path = request.url.path
    for name, value in request.path_params.items():
        path = path.replace(str(value), "{" + name + "}", 1)
    return path


def enforce_unsafe_request_checks(
    db: Session, request: Request, auth: AuthContext, settings: Settings, ctx: RequestContext
) -> None:
    if not has_trusted_origin(request, settings):
        _reject(db, request, auth, ctx, "security.origin_rejected", "Untrusted request origin")
    if not validate_csrf_token(
        request.headers.get(CSRF_HEADER), auth.session.id, settings.secret_key.get_secret_value()
    ):
        _reject(db, request, auth, ctx, "security.csrf_rejected", "CSRF validation failed")


def _reject(
    db: Session, request: Request, auth: AuthContext, ctx: RequestContext, action: str, message: str
) -> None:
    write_audit_event(
        db,
        action=action,
        outcome=AuditOutcome.DENIED,
        actor_user_id=auth.user.id,
        actor_role=auth.user.role,
        ip_address=ctx.ip_address,
        request_id=ctx.request_id,
        details={"http_method": request.method, "route": _route_template(request)},
    )
    db.commit()
    raise HTTPException(status_code=403, detail=message)


def get_auth_context(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ctx: RequestContext = Depends(get_request_context),
) -> AuthContext:
    """Authentication only (session + CSRF/origin on unsafe methods).

    Business routes must NOT use this directly; use `require_roles` / `require_authenticated`,
    which add the mandatory-password-change gate and role check. This bare dependency exists for
    the few routes that must stay reachable while a password change is pending (/auth/me and
    /auth/change-password).
    """
    auth = authenticate(db, request, settings)
    if request.method not in SAFE_METHODS:
        enforce_unsafe_request_checks(db, request, auth, settings, ctx)
    return auth


def deny(
    db: Session,
    request: Request,
    auth: AuthContext,
    ctx: RequestContext,
    *,
    action: str = "authz.denied",
    status_code: int = 403,
    message: str = "Forbidden",
    **details,
) -> HTTPException:
    """Audit + commit an authorization denial, then return the HTTP error to raise."""
    write_audit_event(
        db,
        action=action,
        outcome=AuditOutcome.DENIED,
        actor_user_id=auth.user.id,
        actor_role=auth.user.role,
        ip_address=ctx.ip_address,
        request_id=ctx.request_id,
        details={"http_method": request.method, "route": _route_template(request), **details},
    )
    db.commit()
    return HTTPException(status_code=status_code, detail=message)


def require_roles(*roles: Role):
    """Build a dependency that authenticates, enforces the password-change gate, then the role.

    The user and role come from the database row loaded for THIS request (never from the cookie or
    the client), so deactivation, role changes and password-change requirements apply immediately.
    With no roles given, any authenticated user passes (still subject to the password-change gate).
    """
    allowed = frozenset(role.value for role in roles)

    def dependency(
        request: Request,
        db: Session = Depends(get_db),
        ctx: RequestContext = Depends(get_request_context),
        auth: AuthContext = Depends(get_auth_context),
    ) -> AuthContext:
        if auth.user.must_change_password:
            raise deny(
                db, request, auth, ctx,
                message="Password change required", reason="password_change_required",
            )
        if allowed and auth.user.role not in allowed:
            raise deny(
                db, request, auth, ctx,
                reason="role_not_permitted", required_roles=sorted(allowed),
            )
        return auth

    return dependency


require_authenticated = require_roles()
