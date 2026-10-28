"""Account security: your own sign-in sessions and recent sign-in activity.

  GET  /account/sessions                    your active sessions (the current one is marked)
  POST /account/sessions/{handle}/revoke    end one of your OTHER sessions
  POST /account/sessions/revoke-others      end all your other sessions
  GET  /account/activity                    your recent sign-ins and sign-outs, from the audit trail
  POST /users/{user_id}/sessions/revoke     administrators: end every session of a user (the user is signed out now)

Sessions are server-side rows (see app/security/sessions.py): the browser holds a random token, the database
holds only its hash, and a request is authenticated by looking that row up every time. So revoking a session here
sets `revoked_at` and the very next request that carries it is refused; nothing depends on the browser deleting
anything. The token, its hash and the database id are never returned: each session is addressed by a "handle", an
HMAC of the id under the server secret, which can only be used by the signed-in owner and means nothing elsewhere.

What is shown is deliberately coarse: the browser and system family from the User-Agent, and the network address
with its last part hidden. Activity lists only the caller's own events; failed sign-ins appear only when they
named this account. Every revoke is audited and, like every unsafe request, needs the CSRF token.
"""

import hashlib
import hmac
import ipaddress
import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, RequestContext, get_request_context, require_authenticated, require_roles
from app.audit import AuditOutcome, write_audit_event
from app.config import Settings, get_settings
from app.db import get_db
from app.models import AuditLog, AuthSession, Role, User
from app.security.sessions import SessionStatus, evaluate_session, revoke_session, revoke_user_sessions
from app.timeutil import utcnow

router = APIRouter(tags=["account"])
admin_only = require_roles(Role.ADMIN)
MAX_SESSIONS = 50
ACTIVITY_LIMIT = 20
_EVENTS = {
    "auth.login": "Sign-in", "auth.logout": "Sign-out", "auth.password_change": "Password change", "auth.lockout": "Account locked after failed sign-ins",
    "auth.session_revoked": "A session was ended by you", "auth.sessions_revoked": "Your other sessions were ended",
}


class SessionItem(BaseModel):
    handle: str
    current: bool
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    browser: str
    system: str
    network: str | None


class SessionList(BaseModel):
    items: list[SessionItem]
    idle_timeout_minutes: int
    absolute_timeout_minutes: int


class RevokeResult(BaseModel):
    revoked: int


class ActivityEvent(BaseModel):
    occurred_at: datetime
    event: str
    outcome: str
    network: str | None


class ActivityOut(BaseModel):
    items: list[ActivityEvent]
    previous_sign_in_at: datetime | None  # the successful sign-in before the current one


def handle_for(session_id: uuid.UUID, settings: Settings) -> str:
    key = settings.secret_key.get_secret_value().encode()
    return hmac.new(key, f"session-handle:{session_id}".encode(), hashlib.sha256).hexdigest()[:32]


def describe_agent(user_agent: str | None) -> tuple[str, str]:
    """(browser, system) families only; the raw User-Agent is never returned."""
    ua = user_agent or ""
    browser = next((name for marker, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari")) if marker in ua), "Unknown browser")
    system = next((name for marker, name in (("Windows", "Windows"), ("Android", "Android"), ("iPhone", "iOS"), ("iPad", "iOS"), ("Mac OS X", "macOS"), ("Linux", "Linux")) if re.search(marker, ua)), "Unknown system")
    return browser, system


def mask_network(address: str | None) -> str | None:
    """'203.0.113.57' -> '203.0.113.x'; an IPv6 address keeps only its first three groups."""
    if not address:
        return None
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return None
    if ip.version == 4:
        return ".".join(address.split(".")[:3] + ["x"])
    return ":".join(ip.exploded.split(":")[:3]) + ":…"


def _audit(db: Session, ctx: RequestContext, auth: AuthContext, action: str, **details) -> None:
    write_audit_event(
        db, action=action, outcome=AuditOutcome.SUCCESS, actor_user_id=auth.user.id, actor_role=auth.user.role,
        entity_type="user", entity_id=str(auth.user.id), ip_address=ctx.ip_address, request_id=ctx.request_id, details=details or None,
    )


def _active_sessions(db: Session, user_id: uuid.UUID, settings: Settings) -> list[AuthSession]:
    now = utcnow()
    rows = db.scalars(
        select(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .order_by(AuthSession.last_seen_at.desc()).limit(MAX_SESSIONS)
    ).all()
    return [s for s in rows if evaluate_session(s, settings, now) == SessionStatus.VALID]


@router.get("/account/sessions", response_model=SessionList)
def list_sessions(
    response: Response, db: Session = Depends(get_db), auth: AuthContext = Depends(require_authenticated), settings: Settings = Depends(get_settings),
) -> SessionList:
    response.headers["Cache-Control"] = "no-store"
    items = []
    for s in _active_sessions(db, auth.user.id, settings):
        browser, system = describe_agent(s.user_agent)
        items.append(SessionItem(
            handle=handle_for(s.id, settings), current=s.id == auth.session.id, created_at=s.created_at, last_seen_at=s.last_seen_at,
            expires_at=s.expires_at, browser=browser, system=system, network=mask_network(s.ip_address),
        ))
    items.sort(key=lambda i: (not i.current, -i.last_seen_at.timestamp()))
    return SessionList(items=items, idle_timeout_minutes=settings.session_idle_timeout_minutes, absolute_timeout_minutes=settings.session_absolute_timeout_minutes)


@router.post("/account/sessions/revoke-others", response_model=RevokeResult)
def revoke_others(
    response: Response, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context), auth: AuthContext = Depends(require_authenticated),
) -> RevokeResult:
    response.headers["Cache-Control"] = "no-store"
    count = revoke_user_sessions(db, auth.user.id, except_session_id=auth.session.id)
    _audit(db, ctx, auth, "auth.sessions_revoked", result_count=count)
    db.commit()
    return RevokeResult(revoked=count)


@router.post("/account/sessions/{handle}/revoke", response_model=RevokeResult)
def revoke_one(
    handle: str, request: Request, response: Response, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(require_authenticated), settings: Settings = Depends(get_settings),
) -> RevokeResult:
    response.headers["Cache-Control"] = "no-store"
    target = next((s for s in _active_sessions(db, auth.user.id, settings) if hmac.compare_digest(handle_for(s.id, settings), handle)), None)
    if target is None:  # unknown, already ended, or someone else's: the same answer
        raise HTTPException(status_code=404, detail="That session was not found. It may already have ended.")
    if target.id == auth.session.id:
        raise HTTPException(status_code=409, detail="This is the session you are using now. Use Sign out to end it.")
    revoke_session(target)
    _audit(db, ctx, auth, "auth.session_revoked", reason="user_request")
    db.commit()
    return RevokeResult(revoked=1)


@router.get("/account/activity", response_model=ActivityOut)
def activity(response: Response, db: Session = Depends(get_db), auth: AuthContext = Depends(require_authenticated)) -> ActivityOut:
    response.headers["Cache-Control"] = "no-store"
    rows = db.scalars(
        select(AuditLog).where(AuditLog.actor_user_id == auth.user.id, AuditLog.action.in_(list(_EVENTS)))
        .order_by(AuditLog.seq.desc()).limit(ACTIVITY_LIMIT)
    ).all()
    successes = [r for r in rows if r.action == "auth.login" and r.outcome == AuditOutcome.SUCCESS.value]
    return ActivityOut(
        items=[ActivityEvent(
            occurred_at=r.occurred_at,
            event="Failed sign-in attempt" if r.action == "auth.login" and r.outcome != AuditOutcome.SUCCESS.value else _EVENTS[r.action],
            outcome=r.outcome, network=mask_network(r.ip_address),
        ) for r in rows],
        previous_sign_in_at=successes[1].occurred_at if len(successes) > 1 else None,
    )


@router.post("/users/{user_id}/sessions/revoke", response_model=RevokeResult)
def admin_revoke_sessions(
    user_id: uuid.UUID, response: Response, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_request_context),
    auth: AuthContext = Depends(admin_only),
) -> RevokeResult:
    """End every session of a user, so they must sign in again. Administrators only."""
    response.headers["Cache-Control"] = "no-store"
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    count = revoke_user_sessions(db, target.id)
    write_audit_event(
        db, action="users.sessions_revoked", outcome=AuditOutcome.SUCCESS, actor_user_id=auth.user.id, actor_role=auth.user.role,
        entity_type="user", entity_id=str(target.id), ip_address=ctx.ip_address, request_id=ctx.request_id,
        details={"target_user_id": str(target.id), "result_count": count},
    )
    db.commit()
    return RevokeResult(revoked=count)
