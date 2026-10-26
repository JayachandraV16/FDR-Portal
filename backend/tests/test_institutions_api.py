import json
import uuid

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import User
from tests.api_support import audit_events, audit_rows, create_institution, sign_in


@pytest.fixture
def world(engine):
    return {
        "a": create_institution(engine, "ISO-A", "Institution A (Synthetic)"),
        "b": create_institution(engine, "ISO-B", "Institution B (Synthetic)"),
        "c": create_institution(engine, "ISO-C", "Institution C (Synthetic)"),
    }


def test_institution_user_lists_only_their_own_institution(engine, world):
    user = sign_in(engine, role="INSTITUTION_USER", institution_id=world["a"])
    body = user.get("/institutions").json()
    assert body["total"] == 1 and [i["id"] for i in body["items"]] == [str(world["a"])]
    assert set(body["items"][0]) == {"id", "code", "name", "state", "institution_type",
                                      "contact_email", "status", "created_at"}


@pytest.mark.parametrize("role", ["AICTE_REVIEWER", "AICTE_APPROVER", "ADMIN", "AUDITOR"])
def test_other_roles_see_every_institution(engine, world, role):
    actor = sign_in(engine, role=role)
    body = actor.get("/institutions").json()
    assert {i["id"] for i in body["items"]} == {str(v) for v in world.values()}
    assert body["total"] == 3


def test_institution_user_cannot_fetch_another_institution_and_gets_the_same_404_as_a_missing_one(engine, world):
    user = sign_in(engine, role="INSTITUTION_USER", institution_id=world["a"])
    own = user.get(f"/institutions/{world['a']}")
    other = user.get(f"/institutions/{world['b']}")
    missing = user.get(f"/institutions/{uuid.uuid4()}")
    assert own.status_code == 200 and own.json()["code"] == "ISO-A"
    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json() == {"detail": "Institution not found"}
    denied = audit_events(engine, "authz.scope_denied", "denied")
    assert len(denied) == 2
    assert denied[0].details["target_institution_id"] == str(world["b"])
    assert denied[1].details["target_institution_id"] not in (str(world["a"]), str(world["b"]))
    assert all(e.actor_user_id == user.user_id and e.entity_id is None for e in denied)


def test_client_supplied_institution_ids_never_widen_scope(engine, world):
    user = sign_in(engine, role="INSTITUTION_USER", institution_id=world["a"])
    spoofs = [
        {"params": {"institution_id": str(world["b"])}},
        {"params": {"institution_id": [str(world["b"]), str(world["c"])]}},
        {"headers": {"X-Institution-Id": str(world["b"]), "X-Role": "ADMIN", "X-Forwarded-User": "admin"}},
        {"params": {"limit": 200, "offset": 0, "role": "ADMIN", "scope": "all"}},
    ]
    for kwargs in spoofs:
        body = user.client.get("/api/v1/institutions", **kwargs).json()
        assert [i["id"] for i in body["items"]] == [str(world["a"])] and body["total"] == 1
    assert user.client.get(f"/api/v1/institutions/{world['b']}", params={"institution_id": str(world["b"])}).status_code == 404


def test_scope_follows_the_current_database_row_not_the_login_time_state(engine, world):
    user = sign_in(engine, role="INSTITUTION_USER", institution_id=world["a"])
    assert user.get(f"/institutions/{world['b']}").status_code == 404
    with Session(engine) as s:  # reassigned by an administrator after the user logged in
        s.execute(update(User).where(User.id == user.user_id).values(institution_id=world["b"]))
        s.commit()
    assert user.get(f"/institutions/{world['a']}").status_code == 404
    assert user.get(f"/institutions/{world['b']}").status_code == 200
    assert [i["id"] for i in user.get("/institutions").json()["items"]] == [str(world["b"])]


def test_pagination_and_validation(engine, world):
    admin = sign_in(engine, role="ADMIN")
    page = admin.get("/institutions", params={"limit": 2, "offset": 1}).json()
    assert page["total"] == 3 and [i["code"] for i in page["items"]] == ["ISO-B", "ISO-C"]
    for params in ({"limit": 0}, {"limit": 201}, {"offset": -1}, {"limit": "x"}):
        assert admin.get("/institutions", params=params).status_code == 422
    assert admin.get("/institutions/not-a-uuid").status_code == 422


def test_admin_404_for_missing_institution_is_not_audited_as_scope_denial(engine, world):
    admin = sign_in(engine, role="ADMIN")
    assert admin.get(f"/institutions/{uuid.uuid4()}").status_code == 404
    assert audit_events(engine, "authz.scope_denied") == []


def test_reads_leave_no_audit_noise_and_responses_hold_no_secrets(engine, world):
    user = sign_in(engine, role="INSTITUTION_USER", institution_id=world["a"])
    before = len(audit_rows(engine))
    user.get("/institutions")
    user.get(f"/institutions/{world['a']}")
    assert len(audit_rows(engine)) == before
    text = json.dumps(user.get("/institutions").json()).lower()
    assert "password" not in text and "hash" not in text
