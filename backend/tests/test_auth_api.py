"""Authentication endpoint tests. Every test runs on SQLite, and again on the dedicated PostgreSQL
test database when TEST_DATABASE_URL is set (see the `engine` fixture)."""

import json
from datetime import timedelta

import pytest
from argon2 import PasswordHasher
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit import verify_chain
from app.models import AuthSession, User
from app.security.csrf import generate_csrf_token
from app.security.passwords import verify_password
from app.timeutil import utcnow
from tests.api_support import (
    API,
    NEW_PW,
    ORIGIN,
    PW,
    audit_events,
    audit_rows,
    build_client,
    create_user,
    csrf_headers,
    login,
    make_settings,
)

EMAIL = "admin@example.invalid"


def get_user(engine, email=EMAIL) -> User:
    with Session(engine) as s:
        return s.scalars(select(User).where(User.email == email)).one()


def get_sessions(engine) -> list[AuthSession]:
    with Session(engine) as s:
        return list(s.scalars(select(AuthSession).order_by(AuthSession.created_at)))


def db_update(engine, stmt):
    with Session(engine) as s:
        s.execute(stmt)
        s.commit()


@pytest.fixture
def user_id(engine):
    return create_user(engine)


@pytest.fixture
def client(engine, user_id):
    return build_client(engine)


# ---- login success ---------------------------------------------------------------------------
def test_login_success_returns_user_and_session_bound_csrf_token(client, engine, user_id):
    r = login(client)
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["email"] == EMAIL and body["user"]["role"] == "ADMIN"
    assert set(body["user"]) == {"id", "email", "full_name", "role", "institution_id", "must_change_password"}
    session = get_sessions(engine)[0]
    assert body["csrf_token"] == generate_csrf_token(session.id, make_settings().secret_key.get_secret_value())
    assert r.headers["cache-control"] == "no-store"


def test_login_cookie_flags(client):
    r = login(client)
    cookie = r.headers["set-cookie"].lower()
    assert cookie.startswith("fdr_session=")
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    assert "max-age=28800" in cookie
    assert "secure" not in cookie.replace("samesite", "")  # off only because the test config allows it


def test_secure_flag_is_set_when_configured(engine, user_id):
    c = build_client(engine, make_settings(cookie_secure=True))
    assert "secure" in login(c).headers["set-cookie"].lower().split("; ")


def test_only_the_token_hash_is_stored_and_nothing_secret_is_audited(client, engine, user_id):
    r = login(client)
    raw = client.cookies.get("fdr_session")
    assert raw and raw not in json.dumps(r.json())
    haystack = [str(getattr(s, c.name)) for s in get_sessions(engine) for c in AuthSession.__table__.columns]
    haystack += [json.dumps(row.details) + str(row.hash) for row in audit_rows(engine)]
    blob = " ".join(haystack)
    for secret in (raw, r.json()["csrf_token"], PW, make_settings().secret_key.get_secret_value()):
        assert secret not in blob
    assert len(get_sessions(engine)[0].token_hash) == 64


def test_success_audited_resets_counter_and_records_login_time(client, engine, user_id):
    login(client, password="wrong-password-1")
    assert get_user(engine).failed_login_count == 1
    assert login(client).status_code == 200
    user = get_user(engine)
    assert user.failed_login_count == 0 and user.last_login_at is not None
    (event,) = audit_events(engine, "auth.login", "success")
    assert event.actor_user_id == user.id and event.actor_role == "ADMIN" and event.request_id


def test_each_login_gets_a_fresh_session_and_retires_a_presented_one(client, engine, user_id):
    login(client)
    first = client.cookies.get("fdr_session")
    login(client)  # the browser presents the first cookie
    second = client.cookies.get("fdr_session")
    assert first != second
    sessions = get_sessions(engine)
    assert len(sessions) == 2 and sessions[0].revoked_at is not None and sessions[1].revoked_at is None
    stale = build_client(engine)
    stale.cookies.set("fdr_session", first)
    assert stale.get(f"{API}/auth/me").status_code == 401


def test_outdated_password_hash_is_upgraded_on_login(engine):
    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(PW)
    create_user(engine, password_hash=weak)
    assert login(build_client(engine)).status_code == 200
    stored = get_user(engine).password_hash
    assert stored != weak and verify_password(stored, PW).valid and verify_password(stored, PW).new_hash is None


# ---- login failures: generic and non-enumerating ---------------------------------------------
def test_unknown_email_wrong_password_inactive_and_garbage_are_indistinguishable(engine):
    create_user(engine, email="inactive@example.invalid", is_active=False)
    create_user(engine)
    attempts = [
        ("nobody@example.invalid", PW),
        (EMAIL, "wrong-password-xyz"),
        ("inactive@example.invalid", PW),  # correct password, inactive account
        ("not-an-email", PW),
        ("  ADMIN@Example.invalid ", "wrong-password-xyz"),
    ]
    results = []
    for email, pw in attempts:
        r = login(build_client(engine), email, pw)
        assert "set-cookie" not in r.headers
        results.append((r.status_code, r.json()))
    assert all(res == (401, {"detail": "Invalid credentials"}) for res in results)


def test_failed_login_audit_events_persist_despite_the_401(client, engine, user_id):
    assert login(client, password="wrong-password-1").status_code == 401
    assert login(client, "ghost@example.invalid", PW).status_code == 401
    events = audit_events(engine, "auth.login", "failure")
    assert [e.details["reason"] for e in events] == ["bad_password", "unknown_account"]
    assert events[0].actor_user_id == get_user(engine).id and events[1].actor_user_id is None
    assert events[0].details["failed_login_count"] == 1
    blob = json.dumps([e.details for e in events])
    assert "ghost@example.invalid" not in blob and "wrong-password-1" not in blob


def test_inactive_account_fails_without_touching_counters(engine):
    create_user(engine, is_active=False)
    assert login(build_client(engine)).status_code == 401
    assert get_user(engine).failed_login_count == 0
    assert audit_events(engine, "auth.login", "failure")[0].details["reason"] == "account_inactive"
    assert get_sessions(engine) == []


def test_login_request_validation(client):
    assert client.post(f"{API}/auth/login", json={"email": EMAIL}).status_code == 422
    assert client.post(f"{API}/auth/login", json={"email": EMAIL, "password": PW, "role": "ADMIN"}).status_code == 422
    assert client.post(f"{API}/auth/login", json={"email": EMAIL, "password": "x" * 2000}).status_code == 422


def test_overlong_password_is_a_generic_failure(client):
    assert login(client, password="p" * 500).json() == {"detail": "Invalid credentials"}


def test_login_requires_a_trusted_origin(engine, user_id):
    for origin in (None, "http://evil.example", "null"):
        r = login(build_client(engine, origin=origin))
        assert r.status_code == 403 and "set-cookie" not in r.headers
    assert get_sessions(engine) == [] and audit_rows(engine) == []  # anonymous noise is not audited


# ---- lockout ---------------------------------------------------------------------------------
def test_lockout_threshold_and_expiry(engine, user_id):
    c = build_client(engine, make_settings(login_max_failures=3, login_lockout_minutes=15))
    assert [login(c, password="wrong-password-1").status_code for _ in range(2)] == [401, 401]
    assert audit_events(engine, "auth.lockout") == []
    assert login(c, password="wrong-password-1").status_code == 401  # third failure locks
    user = get_user(engine)
    assert user.locked_until is not None and user.failed_login_count == 0
    (lockout,) = audit_events(engine, "auth.lockout")
    assert lockout.outcome == "denied" and lockout.details == {"reason": "too_many_failures", "locked": True}

    r = login(c)  # correct password while locked: same generic failure, no session
    assert r.status_code == 401 and r.json() == {"detail": "Invalid credentials"} and get_sessions(engine) == []
    assert audit_events(engine, "auth.login", "denied")[0].details["reason"] == "account_locked"

    db_update(engine, update(User).values(locked_until=utcnow() - timedelta(seconds=1)))
    assert login(c).status_code == 200
    user = get_user(engine)
    assert user.locked_until is None and user.failed_login_count == 0


def test_successful_login_resets_the_counter_so_later_failures_do_not_lock(engine, user_id):
    c = build_client(engine, make_settings(login_max_failures=3))
    login(c, password="wrong-password-1")
    login(c, password="wrong-password-1")
    assert login(c).status_code == 200
    login(c, password="wrong-password-1")
    login(c, password="wrong-password-1")
    assert get_user(engine).locked_until is None and get_user(engine).failed_login_count == 2
    assert audit_events(engine, "auth.lockout") == []


# ---- /auth/me and session lifecycle ----------------------------------------------------------
def test_me_requires_a_session(client):
    for cookie in (None, "garbage", "x" * 500):
        c = client
        c.cookies.clear()
        if cookie:
            c.cookies.set("fdr_session", cookie)
        r = c.get(f"{API}/auth/me")
        assert r.status_code == 401 and r.json() == {"detail": "Not authenticated"}
        assert "max-age=0" in r.headers["set-cookie"].lower()


def test_me_returns_current_user_and_csrf_token(client):
    lr = login(client)
    r = client.get(f"{API}/auth/me")
    assert r.status_code == 200 and r.json() == lr.json()
    text = json.dumps(r.json()).lower()
    assert "password_hash" not in text and "argon2" not in text and "failed_login" not in text


@pytest.mark.parametrize(
    "mutation",
    [
        lambda: update(AuthSession).values(last_seen_at=utcnow() - timedelta(minutes=31)),  # idle
        lambda: update(AuthSession).values(expires_at=utcnow() - timedelta(seconds=1)),  # absolute
        lambda: update(AuthSession).values(revoked_at=utcnow()),  # revoked
    ],
    ids=["idle-timeout", "absolute-expiry", "revoked"],
)
def test_expired_or_revoked_sessions_are_rejected_and_the_cookie_cleared(client, engine, mutation):
    login(client)
    db_update(engine, mutation())
    r = client.get(f"{API}/auth/me")
    assert r.status_code == 401 and "max-age=0" in r.headers["set-cookie"].lower()


def test_deactivating_a_user_ends_their_session_immediately(client, engine, user_id):
    login(client)
    db_update(engine, update(User).values(is_active=False))
    assert client.get(f"{API}/auth/me").status_code == 401
    assert get_sessions(engine)[0].revoked_at is not None


# ---- logout ----------------------------------------------------------------------------------
def test_logout_revokes_session_clears_cookie_and_blocks_reuse(client, engine, user_id):
    lr = login(client)
    old_cookie = client.cookies.get("fdr_session")
    r = client.post(f"{API}/auth/logout", headers=csrf_headers(lr))
    assert r.status_code == 200
    cleared = r.headers["set-cookie"].lower()
    assert "max-age=0" in cleared and "httponly" in cleared and "samesite=strict" in cleared
    assert get_sessions(engine)[0].revoked_at is not None
    replay = build_client(engine)
    replay.cookies.set("fdr_session", old_cookie)
    assert replay.get(f"{API}/auth/me").status_code == 401
    assert audit_events(engine, "auth.logout", "success")[0].actor_user_id == user_id


def test_logout_without_a_valid_session_is_idempotent(client):
    r = client.post(f"{API}/auth/logout")
    assert r.status_code == 200 and "max-age=0" in r.headers["set-cookie"].lower()


# ---- CSRF and origin on unsafe cookie-authenticated requests ---------------------------------
@pytest.mark.parametrize("path,body", [("/auth/logout", None), ("/auth/change-password", {"current_password": PW, "new_password": NEW_PW})])
def test_missing_or_wrong_csrf_token_is_rejected_and_audited(client, engine, user_id, path, body):
    login(client)
    for headers in ({}, {"X-CSRF-Token": "wrong"}, {"X-CSRF-Token": ""}):
        r = client.post(f"{API}{path}", json=body, headers=headers)
        assert r.status_code == 403 and r.json() == {"detail": "CSRF validation failed"}
    assert client.get(f"{API}/auth/me").status_code == 200  # session untouched
    events = audit_events(engine, "security.csrf_rejected", "denied")
    assert len(events) == 3 and events[0].actor_user_id == user_id
    assert events[0].details == {"http_method": "POST", "route": f"/api/v1{path}"}
    assert get_user(engine).password_hash and verify_password(get_user(engine).password_hash, PW).valid


@pytest.mark.parametrize("path", ["/auth/logout", "/auth/change-password"])
def test_untrusted_origin_is_rejected_even_with_a_valid_csrf_token(client, engine, path):
    lr = login(client)
    evil = {**csrf_headers(lr), "Origin": "http://evil.example"}
    r = client.post(f"{API}{path}", json={"current_password": PW, "new_password": NEW_PW} if "change" in path else None, headers=evil)
    assert r.status_code == 403 and r.json() == {"detail": "Untrusted request origin"}
    assert client.get(f"{API}/auth/me").status_code == 200
    assert len(audit_events(engine, "security.origin_rejected", "denied")) == 1


def test_referer_is_accepted_when_origin_is_absent(engine, user_id):
    c = build_client(engine, origin=None)
    c.headers["Origin"] = ORIGIN
    lr = login(c)
    del c.headers["Origin"]
    c.headers["Referer"] = f"{ORIGIN}/dashboard"
    assert c.post(f"{API}/auth/logout", headers=csrf_headers(lr)).status_code == 200


def test_safe_methods_need_no_csrf_token(client):
    login(client)
    assert client.get(f"{API}/auth/me").status_code == 200


def test_change_password_without_session_is_401(client):
    r = client.post(f"{API}/auth/change-password", json={"current_password": PW, "new_password": NEW_PW})
    assert r.status_code == 401


# ---- change password -------------------------------------------------------------------------
def test_change_password_revokes_every_session_and_requires_fresh_login(client, engine, user_id):
    other_device = build_client(engine)
    login(other_device)
    lr = login(client)
    r = client.post(f"{API}/auth/change-password", json={"current_password": PW, "new_password": NEW_PW},
                    headers=csrf_headers(lr))
    assert r.status_code == 200 and "max-age=0" in r.headers["set-cookie"].lower()
    assert all(s.revoked_at is not None for s in get_sessions(engine))
    assert client.get(f"{API}/auth/me").status_code == 401
    assert other_device.get(f"{API}/auth/me").status_code == 401

    fresh = build_client(engine)
    assert login(fresh, password=PW).status_code == 401  # old password dead
    assert login(fresh, password=NEW_PW).status_code == 200
    user = get_user(engine)
    assert user.password_changed_at is not None and user.must_change_password is False
    (event,) = audit_events(engine, "auth.password_change", "success")
    assert event.details == {"revoked_count": 2}
    assert NEW_PW not in json.dumps([e.details for e in audit_rows(engine)])


def test_change_password_clears_the_must_change_flag(engine):
    create_user(engine, must_change_password=True)
    c = build_client(engine)
    lr = login(c)
    assert lr.json()["user"]["must_change_password"] is True
    c.post(f"{API}/auth/change-password", json={"current_password": PW, "new_password": NEW_PW}, headers=csrf_headers(lr))
    assert login(build_client(engine), password=NEW_PW).json()["user"]["must_change_password"] is False


def test_wrong_current_password_is_rejected_and_counted(client, engine, user_id):
    lr = login(client)
    r = client.post(f"{API}/auth/change-password", json={"current_password": "wrong-password-1", "new_password": NEW_PW},
                    headers=csrf_headers(lr))
    assert r.status_code == 400 and r.json() == {"detail": "Current password is incorrect"}
    assert client.get(f"{API}/auth/me").status_code == 200  # session intact
    assert verify_password(get_user(engine).password_hash, PW).valid
    assert get_user(engine).failed_login_count == 1
    assert audit_events(engine, "auth.password_change", "failure")[0].details["reason"] == "bad_current_password"


def test_repeated_wrong_current_passwords_lock_the_account_and_kill_sessions(engine, user_id):
    c = build_client(engine, make_settings(login_max_failures=2))
    lr = login(c)
    for _ in range(2):
        c.post(f"{API}/auth/change-password", json={"current_password": "wrong-password-1", "new_password": NEW_PW},
               headers=csrf_headers(lr))
    assert get_user(engine).locked_until is not None
    assert c.get(f"{API}/auth/me").status_code == 401
    assert login(build_client(engine, make_settings(login_max_failures=2))).status_code == 401  # locked
    assert audit_events(engine, "auth.lockout")[0].details["revoked_count"] == 1


@pytest.mark.parametrize(
    "new_password",
    ["short", "a" * 129, EMAIL, PW],
    ids=["too-short", "too-long", "equals-email", "same-as-current"],
)
def test_password_policy_is_enforced(client, engine, user_id, new_password):
    lr = login(client)
    r = client.post(f"{API}/auth/change-password", json={"current_password": PW, "new_password": new_password},
                    headers=csrf_headers(lr))
    assert r.status_code == 400
    assert new_password not in r.text  # error messages never echo the submitted password
    assert verify_password(get_user(engine).password_hash, PW).valid
    assert client.get(f"{API}/auth/me").status_code == 200
    assert audit_events(engine, "auth.password_change", "failure")


# ---- audit chain stays valid across all of the above -----------------------------------------
def test_audit_chain_verifies_after_a_mixed_session(client, engine, user_id):
    login(client, password="wrong-password-1")
    lr = login(client)
    client.post(f"{API}/auth/logout", json=None, headers={"X-CSRF-Token": "bad"})
    client.post(f"{API}/auth/logout", headers=csrf_headers(lr))
    with Session(engine) as s:
        result = verify_chain(s)
    assert result.ok and result.checked >= 4
