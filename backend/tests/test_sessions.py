from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.config import Settings
from app.models import AuthSession
from app.security.sessions import (
    SessionStatus,
    create_session,
    find_session,
    generate_token,
    hash_token,
    revoke_session,
    revoke_user_sessions,
    touch_session,
)

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def settings(idle=30, absolute=480) -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql://u:p@localhost/db",
        secret_key="k" * 32,
        session_idle_timeout_minutes=idle,
        session_absolute_timeout_minutes=absolute,
    )


def test_tokens_are_long_unique_and_url_safe():
    tokens = {generate_token() for _ in range(200)}
    assert len(tokens) == 200
    assert all(len(t) >= 43 for t in tokens)  # 32 random bytes, base64url


def test_hash_is_deterministic_hex_and_differs_from_token():
    t = generate_token()
    assert hash_token(t) == hash_token(t)
    assert len(hash_token(t)) == 64 and hash_token(t) != t
    assert hash_token(t) != hash_token(generate_token())


def test_database_stores_only_the_hash(sqlite_session, make_user):
    user = make_user()
    issued = create_session(sqlite_session, user.id, settings(), now=T0)
    sqlite_session.commit()
    row = sqlite_session.scalar(select(AuthSession))
    stored = [str(getattr(row, c.name)) for c in AuthSession.__table__.columns]
    assert issued.raw_token not in " ".join(stored)
    assert row.token_hash == hash_token(issued.raw_token)


def test_valid_session_found(sqlite_session, make_user):
    user = make_user()
    issued = create_session(sqlite_session, user.id, settings(), now=T0)
    session, status = find_session(sqlite_session, issued.raw_token, settings(), now=T0 + timedelta(minutes=5))
    assert status is SessionStatus.VALID and session.user_id == user.id


def test_unknown_or_malformed_tokens(sqlite_session):
    for token in (None, "", "nope", "x" * 5000):
        assert find_session(sqlite_session, token, settings(), now=T0) == (None, SessionStatus.UNKNOWN)


def test_idle_timeout(sqlite_session, make_user):
    issued = create_session(sqlite_session, make_user().id, settings(idle=30), now=T0)
    _, status = find_session(sqlite_session, issued.raw_token, settings(idle=30), now=T0 + timedelta(minutes=30))
    assert status is SessionStatus.IDLE_TIMEOUT


def test_touch_slides_the_idle_window_but_not_the_absolute_limit(sqlite_session, make_user):
    s = settings(idle=30, absolute=60)
    issued = create_session(sqlite_session, make_user().id, s, now=T0)
    assert touch_session(issued.session, T0 + timedelta(minutes=25)) is True
    _, status = find_session(sqlite_session, issued.raw_token, s, now=T0 + timedelta(minutes=50))
    assert status is SessionStatus.VALID  # 25 min since last seen, within the idle limit
    assert touch_session(issued.session, T0 + timedelta(minutes=55)) is True
    _, status = find_session(sqlite_session, issued.raw_token, s, now=T0 + timedelta(minutes=60))
    assert status is SessionStatus.EXPIRED  # absolute limit wins


def test_touch_is_throttled(sqlite_session, make_user):
    issued = create_session(sqlite_session, make_user().id, settings(), now=T0)
    assert touch_session(issued.session, T0 + timedelta(seconds=10)) is False
    assert touch_session(issued.session, T0 + timedelta(seconds=61)) is True


def test_revoked_session(sqlite_session, make_user):
    issued = create_session(sqlite_session, make_user().id, settings(), now=T0)
    revoke_session(issued.session, T0 + timedelta(minutes=1))
    _, status = find_session(sqlite_session, issued.raw_token, settings(), now=T0 + timedelta(minutes=2))
    assert status is SessionStatus.REVOKED


def test_revoke_user_sessions_spares_the_exception_and_other_users(sqlite_session, make_user):
    a, b = make_user(), make_user()
    keep = create_session(sqlite_session, a.id, settings(), now=T0)
    drop1 = create_session(sqlite_session, a.id, settings(), now=T0)
    drop2 = create_session(sqlite_session, a.id, settings(), now=T0)
    other = create_session(sqlite_session, b.id, settings(), now=T0)
    count = revoke_user_sessions(sqlite_session, a.id, now=T0, except_session_id=keep.session.id)
    sqlite_session.commit()
    for issued in (keep, drop1, drop2, other):
        sqlite_session.refresh(issued.session)
    assert count == 2
    assert drop1.session.revoked_at and drop2.session.revoked_at
    assert keep.session.revoked_at is None and other.session.revoked_at is None
    assert revoke_user_sessions(sqlite_session, a.id, now=T0) == 1  # only `keep` was left
