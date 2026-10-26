"""Authorization must follow the database row, never anything the client or cookie carries."""

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import AuthSession, User
from tests.api_support import API, audit_events, build_client, create_institution, login, sign_in


def db_exec(engine, stmt):
    with Session(engine) as s:
        s.execute(stmt)
        s.commit()


def test_demoting_in_the_database_takes_effect_on_the_next_request(engine):
    actor = sign_in(engine, role="ADMIN")
    assert actor.get("/users").status_code == 200
    db_exec(engine, update(User).where(User.id == actor.user_id).values(role="AUDITOR"))
    assert actor.get("/users").status_code == 403


def test_promoting_in_the_database_takes_effect_on_the_next_request(engine):
    actor = sign_in(engine, role="AUDITOR")
    assert actor.get("/users").status_code == 403
    db_exec(engine, update(User).where(User.id == actor.user_id).values(role="ADMIN"))
    assert actor.get("/users").status_code == 200


def test_deactivated_users_are_rejected_on_every_route_and_lose_their_session(engine):
    actor = sign_in(engine, role="ADMIN")
    db_exec(engine, update(User).where(User.id == actor.user_id).values(is_active=False))
    for path in ("/users", "/institutions", "/auth/me"):
        assert actor.get(path).status_code == 401
    assert actor.post("/auth/logout").status_code == 200  # idempotent: nothing valid left to revoke
    with Session(engine) as s:
        assert all(x.revoked_at for x in s.query(AuthSession).filter_by(user_id=actor.user_id))


def test_inactive_users_cannot_log_in_even_with_the_right_password(engine):
    sign_in(engine, role="ADMIN", email="gone@example.invalid", is_active=False)
    assert login(build_client(engine), "gone@example.invalid").status_code == 401


def test_nothing_in_the_cookie_or_request_headers_can_assert_a_role(engine):
    actor = sign_in(engine, role="AUDITOR")
    cookie = actor.client.cookies.get("fdr_session")
    assert cookie and not any(word in cookie for word in ("AUDITOR", "ADMIN", "role"))
    forged = {"X-Role": "ADMIN", "X-User-Role": "ADMIN", "X-Forwarded-User": "admin", "Authorization": "Bearer admin"}
    assert actor.client.get(f"{API}/users", headers=forged).status_code == 403
    actor.client.cookies.set("role", "ADMIN")
    actor.client.cookies.set("fdr_role", "ADMIN")
    assert actor.client.get(f"{API}/users").status_code == 403


def test_a_role_in_the_request_body_or_query_is_ignored_or_rejected(engine):
    actor = sign_in(engine, role="AUDITOR")
    assert actor.get("/users", params={"role": "ADMIN", "as_role": "ADMIN"}).status_code == 403
    assert actor.post("/users", {"email": "x@example.invalid", "full_name": "X", "role": "ADMIN"}).status_code == 403


def test_no_public_registration_or_privileged_self_service_exists(engine):
    anon = build_client(engine)
    for path in ("/auth/register", "/auth/signup", "/register", "/users/register"):
        assert anon.post(f"{API}{path}", json={"email": "evil@example.invalid", "password": "x" * 20,
                                                "role": "ADMIN"}).status_code in (404, 405)
    assert anon.post(f"{API}/users", json={"email": "evil@example.invalid", "full_name": "E", "role": "ADMIN"}).status_code == 401


def test_a_user_cannot_escalate_through_their_own_record(engine):
    inst = create_institution(engine, "ESC-A", "Escalation Institution (Synthetic)")
    actor = sign_in(engine, role="INSTITUTION_USER", institution_id=inst)
    assert actor.patch(f"/users/{actor.user_id}", {"role": "ADMIN"}).status_code == 403
    assert actor.get(f"/users/{actor.user_id}").status_code == 403
    assert audit_events(engine, "authz.denied", "denied")
