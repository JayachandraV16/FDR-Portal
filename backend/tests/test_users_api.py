import json
import uuid

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

import app.api.users as users_module
from app.audit import verify_chain
from app.models import AuditLog, AuthSession, User
from app.security.passwords import verify_password
from tests.api_support import (
    NEW_PW,
    PW,
    audit_events,
    audit_rows,
    build_client,
    create_institution,
    create_user,
    login,
    sign_in,
)


def body(**kw):
    return {"email": "New.User@Example.invalid", "full_name": "New User (Synthetic)",
            "role": "AICTE_REVIEWER", **kw}


def fetch(engine, user_id=None, email=None) -> User:
    with Session(engine) as s:
        stmt = select(User).where(User.id == user_id) if user_id else select(User).where(User.email == email)
        return s.scalars(stmt).one()


def count_users(engine) -> int:
    with Session(engine) as s:
        return s.scalar(select(func.count()).select_from(User))


@pytest.fixture
def inst(engine):
    return create_institution(engine, "USR-A", "Users Institution A (Synthetic)")


@pytest.fixture
def admin(engine):
    return sign_in(engine, role="ADMIN", email="root@example.invalid")


# ---- create ----------------------------------------------------------------------------------
def test_create_user_returns_one_time_temporary_password_and_forces_a_change(engine, admin):
    r = admin.post("/users", body())
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
    created, temp = r.json()["user"], r.json()["temporary_password"]
    assert created["email"] == "new.user@example.invalid"  # normalised
    assert created["role"] == "AICTE_REVIEWER" and created["is_active"] is True
    assert created["must_change_password"] is True and created["institution_id"] is None
    assert len(temp) >= 20
    assert not {"password_hash", "password"} & set(created)

    stored = fetch(engine, email="new.user@example.invalid")
    assert stored.password_hash.startswith("$argon2id$") and temp not in stored.password_hash
    assert verify_password(stored.password_hash, temp).valid

    fresh = build_client(engine)  # the temporary password works for exactly one thing: signing in
    assert login(fresh, "NEW.user@example.invalid", temp).json()["user"]["must_change_password"] is True


def test_temporary_passwords_are_unique(engine, admin):
    temps = {admin.post("/users", body(email=f"u{i}@example.invalid")).json()["temporary_password"] for i in range(3)}
    assert len(temps) == 3


def test_create_is_audited_without_credentials_or_personal_data(engine, admin, inst):
    r = admin.post("/users", body(role="INSTITUTION_USER", institution_id=str(inst)))
    new_id, temp = r.json()["user"]["id"], r.json()["temporary_password"]
    (event,) = audit_events(engine, "user.create", "success")
    assert event.actor_user_id == admin.user_id and event.actor_role == "ADMIN"
    assert event.entity_type == "user" and event.entity_id == new_id
    assert event.details == {"new_role": "INSTITUTION_USER", "new_is_active": True, "new_institution_id": str(inst)}
    blob = json.dumps([[row.details, row.entity_id, row.hash] for row in audit_rows(engine)])
    for secret in (temp, "new.user@example.invalid", "New User"):
        assert secret not in blob
    with Session(engine) as s:
        assert verify_chain(s).ok


def test_duplicate_email_is_a_conflict_even_with_different_case(engine, admin):
    assert admin.post("/users", body()).status_code == 201
    before = count_users(engine)
    assert admin.post("/users", body(email="  new.user@EXAMPLE.invalid ")).status_code == 409
    assert count_users(engine) == before


@pytest.mark.parametrize(
    "payload",
    [
        {"role": "INSTITUTION_USER"},  # institution required
        {"role": "AUDITOR", "institution_id": str(uuid.uuid4())},  # institution forbidden
        {"role": "ADMIN", "institution_id": str(uuid.uuid4())},
        {"role": "SUPERUSER"},
        {"role": "admin"},  # roles are case-sensitive
        {"role": "INSTITUTION_USER", "institution_id": str(uuid.uuid4())},  # unknown institution
        {"email": "not-an-email"},
        {"email": ""},
        {"full_name": "   "},
        {"password": "hunter2hunter2"},  # clients cannot choose a password
        {"is_active": False},
        {"must_change_password": False},
    ],
)
def test_invalid_creation_requests_are_rejected_with_no_side_effects(engine, admin, payload):
    before, audit_before = count_users(engine), len(audit_rows(engine))
    assert admin.post("/users", body(**payload)).status_code == 422
    assert count_users(engine) == before and len(audit_rows(engine)) == audit_before


def test_missing_fields_rejected(engine, admin):
    assert admin.post("/users", {"email": "x@example.invalid"}).status_code == 422


# ---- read ------------------------------------------------------------------------------------
def test_list_filter_paginate_and_get(engine, admin, inst):
    create_user(engine, email="rev@example.invalid", role="AICTE_REVIEWER")
    create_user(engine, email="off@example.invalid", role="AUDITOR", is_active=False)
    create_user(engine, email="inst@example.invalid", role="INSTITUTION_USER", institution_id=inst)
    everyone = admin.get("/users").json()
    assert everyone["total"] == 4
    assert admin.get("/users", params={"role": "AUDITOR"}).json()["total"] == 1
    assert admin.get("/users", params={"is_active": "false"}).json()["items"][0]["email"] == "off@example.invalid"
    assert admin.get("/users", params={"institution_id": str(inst)}).json()["total"] == 1
    page = admin.get("/users", params={"limit": 2, "offset": 2}).json()
    assert page["total"] == 4 and len(page["items"]) == 2
    assert admin.get("/users", params={"role": "BOGUS"}).status_code == 422
    one = admin.get(f"/users/{everyone['items'][0]['id']}")
    assert one.status_code == 200
    text = json.dumps([everyone, one.json()]).lower()
    assert "argon2" not in text and "password_hash" not in text and "token" not in text


def test_get_unknown_user_is_404(admin):
    assert admin.get(f"/users/{uuid.uuid4()}").status_code == 404
    assert admin.get("/users/not-a-uuid").status_code == 422


# ---- update ----------------------------------------------------------------------------------
def test_role_change_to_institution_user_requires_an_institution_and_back_clears_it(engine, admin, inst):
    target = create_user(engine, email="t@example.invalid", role="AICTE_REVIEWER")
    assert admin.patch(f"/users/{target}", {"role": "INSTITUTION_USER"}).status_code == 422
    r = admin.patch(f"/users/{target}", {"role": "INSTITUTION_USER", "institution_id": str(inst)})
    assert r.status_code == 200 and r.json()["institution_id"] == str(inst)
    assert admin.patch(f"/users/{target}", {"institution_id": None}).status_code == 422  # would violate the rule
    r = admin.patch(f"/users/{target}", {"role": "AUDITOR"})  # leaving INSTITUTION_USER clears it
    assert r.status_code == 200 and r.json()["institution_id"] is None
    assert admin.patch(f"/users/{target}", {"institution_id": str(inst)}).status_code == 422
    assert fetch(engine, target).institution_id is None


def test_institution_reassignment_and_unknown_institution(engine, admin, inst):
    other = create_institution(engine, "USR-B", "Users Institution B (Synthetic)")
    target = create_user(engine, email="t@example.invalid", role="INSTITUTION_USER", institution_id=inst)
    assert admin.patch(f"/users/{target}", {"institution_id": str(uuid.uuid4())}).status_code == 422
    assert admin.patch(f"/users/{target}", {"institution_id": str(other)}).status_code == 200
    (event,) = audit_events(engine, "user.institution_change")
    assert event.details == {"old_institution_id": str(inst), "new_institution_id": str(other)}
    assert event.entity_id == str(target) and event.actor_user_id == admin.user_id


def test_role_change_is_audited_and_effective_immediately(engine, admin):
    reviewer = sign_in(engine, role="AICTE_REVIEWER")
    assert reviewer.get("/institutions").status_code == 200
    assert admin.patch(f"/users/{reviewer.user_id}", {"role": "ADMIN"}).status_code == 200
    assert reviewer.get("/users").status_code == 200  # same session, new authority, no re-login
    (event,) = audit_events(engine, "user.role_change")
    assert event.details == {"old_role": "AICTE_REVIEWER", "new_role": "ADMIN"}


def test_deactivation_revokes_sessions_blocks_login_and_reactivation_restores(engine, admin):
    victim = sign_in(engine, role="AUDITOR", email="victim@example.invalid")
    assert admin.patch(f"/users/{victim.user_id}", {"is_active": False}).status_code == 200
    assert victim.get("/institutions").status_code == 401  # session was revoked at once
    with Session(engine) as s:
        assert all(x.revoked_at for x in s.scalars(select(AuthSession).where(AuthSession.user_id == victim.user_id)))
    assert login(build_client(engine), "victim@example.invalid").status_code == 401
    (deact,) = audit_events(engine, "user.deactivate")
    assert deact.details == {"old_is_active": True, "new_is_active": False, "revoked_count": 1}
    assert admin.patch(f"/users/{victim.user_id}", {"is_active": True}).status_code == 200
    assert audit_events(engine, "user.activate")
    assert login(build_client(engine), "victim@example.invalid").status_code == 200


def test_full_name_update_audits_field_names_only(engine, admin):
    target = create_user(engine, email="t@example.invalid", role="AUDITOR")
    assert admin.patch(f"/users/{target}", {"full_name": "Renamed Person"}).json()["full_name"] == "Renamed Person"
    (event,) = audit_events(engine, "user.update")
    assert event.details == {"changed_fields": ["full_name"]}
    assert "Renamed" not in json.dumps(event.details)


def test_noop_patch_succeeds_without_an_audit_event(engine, admin):
    target = create_user(engine, email="t@example.invalid", role="AUDITOR")
    before = len(audit_rows(engine))
    assert admin.patch(f"/users/{target}", {"role": "AUDITOR", "is_active": True}).status_code == 200
    assert len(audit_rows(engine)) == before


@pytest.mark.parametrize(
    "payload",
    [{}, {"role": None}, {"is_active": None}, {"role": "NOPE"}, {"email": "x@example.invalid"},
     {"password": "hunter2hunter2"}, {"must_change_password": False}, {"full_name": ""}],
)
def test_invalid_patch_bodies(engine, admin, payload):
    target = create_user(engine, email="t@example.invalid", role="AUDITOR")
    assert admin.patch(f"/users/{target}", payload).status_code == 422


def test_patch_unknown_user_is_404(admin):
    assert admin.patch(f"/users/{uuid.uuid4()}", {"is_active": False}).status_code == 404


def test_a_multi_step_update_is_atomic_when_a_later_audit_write_fails(engine, inst, monkeypatch):
    admin = sign_in(engine, role="ADMIN", raise_server_exceptions=False)
    target = create_user(engine, email="t@example.invalid", role="INSTITUTION_USER", institution_id=inst)
    real, calls = users_module.write_audit_event, {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated audit failure")
        return real(*args, **kwargs)

    monkeypatch.setattr(users_module, "write_audit_event", flaky)
    # Role change (audit #1 succeeds) + institution clearing (audit #2 explodes).
    r = admin.patch(f"/users/{target}", {"role": "AICTE_REVIEWER"})
    assert r.status_code == 500
    row = fetch(engine, target)
    assert row.role == "INSTITUTION_USER" and row.institution_id == inst  # nothing half-applied
    assert audit_events(engine, "user.role_change") == [] and audit_events(engine, "user.institution_change") == []
    monkeypatch.setattr(users_module, "write_audit_event", real)
    assert admin.patch(f"/users/{target}", {"role": "AICTE_REVIEWER"}).status_code == 200  # still works afterwards


def test_a_failed_audit_on_create_leaves_no_user_behind(engine, monkeypatch):
    admin = sign_in(engine, role="ADMIN", raise_server_exceptions=False)
    before = count_users(engine)

    def boom(*a, **k):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(users_module, "write_audit_event", boom)
    assert admin.post("/users", body()).status_code == 500
    assert count_users(engine) == before


# ---- last active administrator ---------------------------------------------------------------
def test_sole_admin_cannot_demote_or_deactivate_themselves(engine, admin):
    for payload in ({"role": "AUDITOR"}, {"is_active": False}, {"role": "AUDITOR", "is_active": False}):
        r = admin.patch(f"/users/{admin.user_id}", payload)
        assert r.status_code == 409 and r.json() == {"detail": "This change would leave no active administrator"}
    row = fetch(engine, admin.user_id)
    assert row.role == "ADMIN" and row.is_active
    denied = audit_events(engine, "authz.denied", "denied")
    assert len(denied) == 3 and all(e.details["reason"] == "last_admin_protection" for e in denied)


def test_inactive_admins_do_not_count_as_a_remaining_administrator(engine, admin):
    create_user(engine, email="dormant@example.invalid", role="ADMIN", is_active=False)
    assert admin.patch(f"/users/{admin.user_id}", {"role": "AUDITOR"}).status_code == 409


def test_admin_can_step_down_when_another_active_admin_exists_and_the_last_one_is_then_protected(engine, admin):
    other = sign_in(engine, role="ADMIN", email="second@example.invalid")
    assert admin.patch(f"/users/{admin.user_id}", {"role": "AUDITOR"}).status_code == 200
    assert admin.get("/users").status_code == 403  # lost authority immediately
    assert other.patch(f"/users/{other.user_id}", {"is_active": False}).status_code == 409
    assert other.patch(f"/users/{admin.user_id}", {"role": "ADMIN"}).status_code == 200  # promoting restores
    assert admin.patch(f"/users/{other.user_id}", {"is_active": False}).status_code == 200


def test_admin_deactivating_themselves_clears_their_cookie(engine, admin):
    other = sign_in(engine, role="ADMIN", email="second@example.invalid")
    r = admin.patch(f"/users/{admin.user_id}", {"is_active": False})
    assert r.status_code == 200 and "max-age=0" in r.headers["set-cookie"].lower()
    assert other.get("/users").status_code == 200


def test_an_admin_with_a_pending_password_change_cannot_use_admin_endpoints(engine):
    pending = sign_in(engine, role="ADMIN", must_change_password=True)
    assert pending.get("/users").status_code == 403
    assert pending.patch(f"/users/{pending.user_id}", {"full_name": "X"}).status_code == 403
    assert pending.post("/users", body()).status_code == 403


def test_no_user_can_end_up_with_a_password_that_is_not_hashed(engine, admin):
    admin.post("/users", body())
    with Session(engine) as s:
        assert all(h.startswith("$argon2id$") for h in s.scalars(select(User.password_hash)))
