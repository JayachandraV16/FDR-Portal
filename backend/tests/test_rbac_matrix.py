"""Every role against every Stage 4 endpoint (SQLite, and PostgreSQL when TEST_DATABASE_URL is set)."""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AuditLog, Role, User
from tests.api_support import (
    API,
    audit_events,
    build_client,
    create_institution,
    create_user,
    sign_in,
)

ROLES = [r.value for r in Role]
ADMIN_ONLY = {"ADMIN"}
EVERYONE = set(ROLES)

# name -> (method, path template, body, roles allowed for the "happy" status, happy status)
# {A}/{B} are institution IDs (the INSTITUTION_USER belongs to A); {T} is a throwaway user ID.
ENDPOINTS = {
    "list_institutions": ("GET", "/institutions", None, EVERYONE, 200),
    "get_own_institution": ("GET", "/institutions/{A}", None, EVERYONE, 200),
    "list_users": ("GET", "/users", None, ADMIN_ONLY, 200),
    "get_user": ("GET", "/users/{T}", None, ADMIN_ONLY, 200),
    "create_user": (
        "POST", "/users",
        {"email": "created@example.invalid", "full_name": "Created (Synthetic)", "role": "AUDITOR"},
        ADMIN_ONLY, 201,
    ),
    "patch_user": ("PATCH", "/users/{T}", {"full_name": "Renamed (Synthetic)"}, ADMIN_ONLY, 200),
}


@pytest.fixture
def world(engine):
    a = create_institution(engine, "RBAC-A", "Institution A (Synthetic)")
    b = create_institution(engine, "RBAC-B", "Institution B (Synthetic)")
    target = create_user(engine, email="target@example.invalid", role="AUDITOR")
    return {"A": str(a), "B": str(b), "T": str(target)}


def _actor(engine, role, world):
    return sign_in(engine, role=role, institution_id=uuid.UUID(world["A"]) if role == "INSTITUTION_USER" else None)


def _call(actor, method, path, body, world):
    return actor.client.request(method, f"{API}{path.format(**world)}", json=body, headers=actor.headers)


@pytest.mark.parametrize("name", ENDPOINTS)
@pytest.mark.parametrize("role", ROLES)
def test_role_matrix(engine, world, role, name):
    method, path, body, allowed, ok_status = ENDPOINTS[name]
    actor = _actor(engine, role, world)
    response = _call(actor, method, path, body, world)
    if role in allowed:
        assert response.status_code == ok_status, response.text
    else:
        assert response.status_code == 403 and response.json() == {"detail": "Forbidden"}
        (event,) = [e for e in audit_events(engine, "authz.denied") if e.actor_user_id == actor.user_id]
        assert event.outcome == "denied" and event.details["reason"] == "role_not_permitted"
        assert event.details["required_roles"] == ["ADMIN"]
        assert event.details["http_method"] == method


@pytest.mark.parametrize("name", ENDPOINTS)
def test_unauthenticated_requests_get_401(engine, world, name):
    method, path, body, *_ = ENDPOINTS[name]
    client = build_client(engine)
    response = client.request(method, f"{API}{path.format(**world)}", json=body)
    assert response.status_code == 401 and response.json() == {"detail": "Not authenticated"}


@pytest.mark.parametrize("role", [r for r in ROLES if r != "ADMIN"])
def test_forbidden_writes_change_nothing(engine, world, role):
    actor = _actor(engine, role, world)
    with Session(engine) as s:
        users_before = s.scalar(select(func.count()).select_from(User))
    for name in ("create_user", "patch_user"):
        method, path, body, *_ = ENDPOINTS[name]
        assert _call(actor, method, path, body, world).status_code == 403
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(User)) == users_before
        target = s.get(User, uuid.UUID(world["T"]))
        assert target.full_name == "Test User (Synthetic)"
        assert not s.scalars(select(AuditLog).where(AuditLog.action.like("user.%"))).all()


def test_auditor_is_read_only_everywhere(engine, world):
    auditor = _actor(engine, "AUDITOR", world)
    assert auditor.get("/institutions").status_code == 200
    for method, path in (("POST", "/institutions"), ("PATCH", "/institutions/{A}"), ("DELETE", "/institutions/{A}"),
                         ("POST", "/users"), ("PATCH", "/users/{T}"), ("DELETE", "/users/{T}")):
        status = _call(auditor, method, path, {}, world).status_code
        assert status in (403, 405, 422) and status != 200


def test_institution_endpoints_expose_no_write_methods(engine, world):
    admin = _actor(engine, "ADMIN", world)
    for method, path in (("POST", "/institutions"), ("PUT", "/institutions/{A}"), ("PATCH", "/institutions/{A}"),
                         ("DELETE", "/institutions/{A}")):
        assert _call(admin, method, path, {}, world).status_code == 405
