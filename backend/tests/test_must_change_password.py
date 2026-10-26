"""Mandatory password change, exercised through direct API calls (no frontend involved)."""

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import User
from tests.api_support import (
    API,
    NEW_PW,
    PW,
    audit_events,
    build_client,
    create_institution,
    create_user,
    login,
    sign_in,
)

BLOCKED = [
    ("GET", "/institutions", None),
    ("GET", "/users", None),
    ("POST", "/users", {"email": "x@example.invalid", "full_name": "X", "role": "AUDITOR"}),
]


@pytest.fixture
def pending(engine):
    return sign_in(engine, role="ADMIN", email="pending@example.invalid", must_change_password=True)


@pytest.mark.parametrize("method,path,payload", BLOCKED)
def test_protected_routes_are_blocked_until_the_password_is_changed(engine, pending, method, path, payload):
    r = pending.client.request(method, f"{API}{path}", json=payload, headers=pending.headers)
    assert r.status_code == 403 and r.json() == {"detail": "Password change required"}


def test_blocked_attempts_are_audited(engine, pending):
    pending.get("/institutions")
    (event,) = audit_events(engine, "authz.denied", "denied")
    assert event.actor_user_id == pending.user_id
    assert event.details == {"http_method": "GET", "route": "/api/v1/institutions", "reason": "password_change_required"}


def test_the_minimal_state_endpoint_logout_and_change_password_remain_available(engine, pending):
    me = pending.get("/auth/me")
    assert me.status_code == 200 and me.json()["user"]["must_change_password"] is True
    # A wrong current password is a normal 400 from the endpoint, not a 403 from the gate.
    bad = pending.post("/auth/change-password", {"current_password": "wrong-password-1", "new_password": NEW_PW})
    assert bad.status_code == 400
    ok = pending.post("/auth/change-password", {"current_password": PW, "new_password": NEW_PW})
    assert ok.status_code == 200


def test_completing_the_change_unlocks_access_after_a_fresh_login(engine, pending):
    assert pending.get("/users").status_code == 403
    pending.post("/auth/change-password", {"current_password": PW, "new_password": NEW_PW})
    assert pending.get("/users").status_code == 401  # old session was revoked by the change
    fresh = build_client(engine)
    assert login(fresh, "pending@example.invalid", NEW_PW).json()["user"]["must_change_password"] is False
    assert fresh.get(f"{API}/users").status_code == 200


def test_logout_works_while_a_change_is_pending(engine, pending):
    assert pending.post("/auth/logout").status_code == 200
    assert pending.get("/auth/me").status_code == 401


def test_gate_applies_to_every_role(engine):
    inst = create_institution(engine, "MCP-A", "MCP Institution (Synthetic)")
    for role in ("AICTE_REVIEWER", "AICTE_APPROVER", "AUDITOR", "INSTITUTION_USER"):
        actor = sign_in(engine, role=role, must_change_password=True,
                        institution_id=inst if role == "INSTITUTION_USER" else None)
        assert actor.get("/institutions").status_code == 403, role


def test_the_gate_reads_the_current_database_flag(engine):
    actor = sign_in(engine, role="AUDITOR")
    assert actor.get("/institutions").status_code == 200
    with Session(engine) as s:  # e.g. an administrator forces a reset mid-session
        s.execute(update(User).where(User.id == actor.user_id).values(must_change_password=True))
        s.commit()
    assert actor.get("/institutions").status_code == 403
    assert actor.get("/auth/me").status_code == 200


def test_end_to_end_new_account_flow(engine):
    admin = sign_in(engine, role="ADMIN")
    created = admin.post("/users", {"email": "fresh@example.invalid", "full_name": "Fresh (Synthetic)",
                                     "role": "AICTE_APPROVER"}).json()
    new = build_client(engine)
    first = login(new, "fresh@example.invalid", created["temporary_password"])
    assert first.status_code == 200
    assert new.get(f"{API}/institutions").status_code == 403  # blocked with the temporary password
    r = new.post(f"{API}/auth/change-password", headers={"X-CSRF-Token": first.json()["csrf_token"]},
                 json={"current_password": created["temporary_password"], "new_password": NEW_PW})
    assert r.status_code == 200
    assert login(new, "fresh@example.invalid", created["temporary_password"]).status_code == 401
    assert login(new, "fresh@example.invalid", NEW_PW).status_code == 200
    assert new.get(f"{API}/institutions").status_code == 200
