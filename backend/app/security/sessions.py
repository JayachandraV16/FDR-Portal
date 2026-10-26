"""Opaque server-side sessions.

The browser holds a random token; the database holds only its SHA-256 digest. A leaked database
therefore cannot be replayed as cookies. Nothing here logs or returns the digest to clients.
"""

import enum
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AuthSession
from app.timeutil import as_utc, utcnow

TOKEN_BYTES = 32  # 256 bits of entropy
MAX_TOKEN_LENGTH = 128  # anything longer cannot be one of ours; skip the DB lookup
TOUCH_INTERVAL = timedelta(seconds=60)  # avoid a write on every request


class SessionStatus(enum.StrEnum):
    VALID = "valid"
    UNKNOWN = "unknown"
    REVOKED = "revoked"
    EXPIRED = "expired"  # absolute lifetime reached
    IDLE_TIMEOUT = "idle_timeout"


@dataclass(frozen=True)
class IssuedSession:
    raw_token: str  # goes into the cookie only; never store or log it
    session: AuthSession


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_session(
    db: Session,
    user_id: uuid.UUID,
    settings: Settings,
    *,
    now: datetime | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> IssuedSession:
    now = as_utc(now or utcnow())
    raw = generate_token()
    session = AuthSession(
        user_id=user_id,
        token_hash=hash_token(raw),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(minutes=settings.session_absolute_timeout_minutes),
        ip_address=ip_address[:45] if ip_address else None,
        user_agent=user_agent[:255] if user_agent else None,
    )
    db.add(session)
    db.flush()
    return IssuedSession(raw_token=raw, session=session)


def evaluate_session(session: AuthSession, settings: Settings, now: datetime) -> SessionStatus:
    now = as_utc(now)
    if session.revoked_at is not None:
        return SessionStatus.REVOKED
    if as_utc(session.expires_at) <= now:
        return SessionStatus.EXPIRED
    idle_limit = as_utc(session.last_seen_at) + timedelta(minutes=settings.session_idle_timeout_minutes)
    if idle_limit <= now:
        return SessionStatus.IDLE_TIMEOUT
    return SessionStatus.VALID


def find_session(
    db: Session, raw_token: str | None, settings: Settings, *, now: datetime | None = None
) -> tuple[AuthSession | None, SessionStatus]:
    """Look a token up. Returns (session, status); session is None only for UNKNOWN."""
    if not raw_token or len(raw_token) > MAX_TOKEN_LENGTH:
        return None, SessionStatus.UNKNOWN
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(raw_token)))
    if session is None:
        return None, SessionStatus.UNKNOWN
    return session, evaluate_session(session, settings, now or utcnow())


def touch_session(session: AuthSession, now: datetime | None = None) -> bool:
    """Slide the idle window; writes at most once per TOUCH_INTERVAL. Returns True if updated."""
    now = as_utc(now or utcnow())
    if now - as_utc(session.last_seen_at) < TOUCH_INTERVAL:
        return False
    session.last_seen_at = now
    return True


def revoke_session(session: AuthSession, now: datetime | None = None) -> None:
    if session.revoked_at is None:
        session.revoked_at = as_utc(now or utcnow())


def revoke_user_sessions(
    db: Session,
    user_id: uuid.UUID,
    *,
    now: datetime | None = None,
    except_session_id: uuid.UUID | None = None,
) -> int:
    """Revoke every active session of a user (optionally sparing one). Returns the count."""
    stmt = (
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=as_utc(now or utcnow()))
    )
    if except_session_id is not None:
        stmt = stmt.where(AuthSession.id != except_session_id)
    return db.execute(stmt).rowcount
