"""Institution-side FDR workflow: create a draft, edit it, submit it for review.

Runs on SQLite and, when TEST_DATABASE_URL is set, on the dedicated PostgreSQL test database (the
`engine` fixture). The concurrency tests need real row locks and are PostgreSQL-only. All data is
synthetic.
"""

import json
import threading
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import verify_chain
from app.models import FDR, AuditLog, Bank, FDRRevision, FDRStatusHistory
from tests.api_support import API, audit_events, audit_rows, build_client, create_institution, create_user, sign_in
from tests.fdr_support import sqlite_decimal_warning

pytestmark = sqlite_decimal_warning

CROSS_INSTITUTION_ROLES = ["AICTE_REVIEWER", "AICTE_APPROVER", "ADMIN", "AUDITOR"]


@pytest.fixture
def world(engine):
    inst_a = create_institution(engine, "WR-A", "Write Institution A (Synthetic)")
    inst_b = create_institution(engine, "WR-B", "Write Institution B (Synthetic)")
    with Session(engine) as s:
        bank1 = Bank(code="WR-B1", name="Write Bank 1 (Synthetic)")
        bank2 = Bank(code="WR-B2", name="Write Bank 2 (Synthetic)")
        s.add_all([bank1, bank2])
        s.commit()
        banks = (bank1.id, bank2.id)
    return {"A": inst_a, "B": inst_b, "bank1": str(banks[0]), "bank2": str(banks[1])}


def institution_user(engine, world, inst="A", email=None):
    return sign_in(engine, role="INSTITUTION_USER", institution_id=world[inst], email=email)


def payload(world, **overrides):
    values = {
        "bank_id": world["bank1"],
        "fdr_number": "WR-FDR-001",
        "branch_name": "Main Branch (Synthetic)",
        "depositor_name": "Test Depositor (Synthetic)",
        "purpose": "Security deposit",
        "principal_amount": "1500000.00",
        "interest_rate_pct": "7.10",
        "issue_date": "2026-01-15",
        "maturity_date": "2027-01-15",
    }
    values.update(overrides)
    return {k: v for k, v in values.items() if v is not ...}


def create(actor, world, **overrides):
    r = actor.post("/fdrs", payload(world, **overrides))
    assert r.status_code == 201, r.text
    return r.json()


def row(engine, fdr_id) -> FDR:
    with Session(engine) as s:
        return s.get(FDR, uuid.UUID(str(fdr_id)))


def count(engine, model) -> int:
    with Session(engine) as s:
        return s.scalar(select(func.count()).select_from(model))


def history(engine, fdr_id):
    with Session(engine) as s:
        return list(s.scalars(select(FDRStatusHistory).where(FDRStatusHistory.fdr_id == uuid.UUID(str(fdr_id))).order_by(FDRStatusHistory.id)))


def revisions(engine, fdr_id):
    with Session(engine) as s:
        return list(s.scalars(select(FDRRevision).where(FDRRevision.fdr_id == uuid.UUID(str(fdr_id)))))


def force_status(engine, fdr_id, status):
    with Session(engine) as s:
        fdr = s.get(FDR, uuid.UUID(str(fdr_id)))
        fdr.status = status
        s.commit()


# ---- create ---------------------------------------------------------------------------------
def test_creates_a_draft_in_the_callers_own_institution(engine, world):
    owner = institution_user(engine, world)
    body = create(owner, world, fdr_number="  WR-FDR-001  ")
    assert body["status"] == "DRAFT" and body["row_version"] == 1
    assert body["institution"]["id"] == str(world["A"])
    assert body["fdr_number"] == "WR-FDR-001"  # surrounding whitespace trimmed
    assert body["principal_amount"] == "1500000.00" and body["interest_rate_pct"] == "7.10"
    assert body["submitted_at"] is None and body["decided_at"] is None
    assert set(body) == {  # no internal fields leak
        "id", "institution", "bank", "fdr_number", "branch_name", "depositor_name", "purpose", "currency",
        "principal_amount", "interest_rate_pct", "issue_date", "maturity_date", "status", "row_version",
        "created_at", "updated_at", "submitted_at", "decided_at",
    }
    stored = row(engine, body["id"])
    assert stored.created_by == owner.user_id and stored.fdr_number_normalized == "WR-FDR-001"
    (entry,) = history(engine, body["id"])
    assert (entry.from_status, entry.to_status, entry.actor_user_id, entry.actor_role) == (None, "DRAFT", owner.user_id, "INSTITUTION_USER")
    assert revisions(engine, body["id"]) == []  # snapshots exist only once submitted


def test_creation_is_visible_in_the_registry_to_its_owner_but_not_to_reviewers(engine, world):
    owner = institution_user(engine, world)
    body = create(owner, world)
    assert [i["id"] for i in owner.get("/fdrs").json()["items"]] == [body["id"]]
    reviewer = sign_in(engine, role="AICTE_REVIEWER")
    assert reviewer.get("/fdrs").json()["total"] == 0  # drafts are private to the institution
    assert reviewer.get(f"/fdrs/{body['id']}").status_code == 404


def test_optional_fields_may_be_omitted_or_blank(engine, world):
    owner = institution_user(engine, world)
    body = create(owner, world, branch_name=..., depositor_name="   ", purpose=None, interest_rate_pct=None)
    assert body["branch_name"] is None and body["depositor_name"] is None
    assert body["purpose"] is None and body["interest_rate_pct"] is None


@pytest.mark.parametrize("forbidden", [
    {"institution_id": str(uuid.uuid4())}, {"status": "ACCEPTED"}, {"created_by": str(uuid.uuid4())},
    {"row_version": 9}, {"submitted_at": "2026-01-01T00:00:00Z"}, {"id": str(uuid.uuid4())},
])
def test_the_client_cannot_set_server_controlled_fields(engine, world, forbidden):
    owner = institution_user(engine, world)
    r = owner.post("/fdrs", payload(world, **forbidden))
    assert r.status_code == 422
    assert count(engine, FDR) == 0


def test_a_foreign_institution_id_is_rejected_and_the_record_never_lands_there(engine, world):
    owner = institution_user(engine, world, "A")
    r = owner.post("/fdrs", payload(world, institution_id=str(world["B"])))
    assert r.status_code == 422 and count(engine, FDR) == 0
    body = create(owner, world)
    assert row(engine, body["id"]).institution_id == world["A"]


@pytest.mark.parametrize("overrides", [
    {"principal_amount": "0"}, {"principal_amount": "-5.00"}, {"principal_amount": "1.234"},
    {"principal_amount": "1000000000000.00"}, {"principal_amount": "abc"}, {"principal_amount": ...},
    {"interest_rate_pct": "100.01"}, {"interest_rate_pct": "-0.01"}, {"interest_rate_pct": "7.123"},
    {"maturity_date": "2026-01-15"}, {"maturity_date": "2025-12-31"}, {"issue_date": "not-a-date"},
    {"issue_date": ...}, {"maturity_date": ...},
    {"fdr_number": ""}, {"fdr_number": "   "}, {"fdr_number": "x" * 65}, {"fdr_number": ...},
    {"bank_id": "not-a-uuid"}, {"bank_id": ...}, {"bank_id": str(uuid.uuid4())},
    {"branch_name": "x" * 201}, {"depositor_name": "x" * 301}, {"purpose": "x" * 201},
])
def test_invalid_input_is_rejected_and_nothing_is_written(engine, world, overrides):
    owner = institution_user(engine, world)
    before = len(audit_rows(engine))
    r = owner.post("/fdrs", payload(world, **overrides))
    assert r.status_code == 422, r.text
    assert count(engine, FDR) == 0 and count(engine, FDRStatusHistory) == 0
    assert len(audit_rows(engine)) == before  # a rejected request writes no fdr.* audit event
    assert all(not e.action.startswith("fdr.") for e in audit_rows(engine))


def test_two_drafts_may_share_a_number_because_only_live_records_must_be_unique(engine, world):
    owner = institution_user(engine, world)
    create(owner, world)
    create(owner, world)
    assert count(engine, FDR) == 2


def test_creation_audit_event_records_field_names_only(engine, world):
    owner = institution_user(engine, world)
    body = create(owner, world, principal_amount="123456789.00", depositor_name="Secret Depositor Name")
    (event,) = audit_events(engine, "fdr.create", "success")
    assert (event.entity_type, event.entity_id) == ("fdr", body["id"])
    assert event.actor_user_id == owner.user_id and event.actor_role == "INSTITUTION_USER"
    assert event.details == {"changed_fields": sorted(payload(world, principal_amount="x", depositor_name="x"))}
    blob = json.dumps(event.details) + str(event.entity_id)
    assert "123456789" not in blob and "Secret Depositor Name" not in blob and "WR-FDR-001" not in blob
    with Session(engine) as s:
        assert verify_chain(s).ok


# ---- edit -----------------------------------------------------------------------------------
def test_edits_a_draft_and_bumps_the_version(engine, world):
    owner = institution_user(engine, world)
    draft = create(owner, world)
    r = owner.patch(f"/fdrs/{draft['id']}", {"row_version": 1, "purpose": "Updated purpose", "principal_amount": "2000000.50"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["row_version"], body["purpose"], body["principal_amount"], body["status"]) == (2, "Updated purpose", "2000000.50", "DRAFT")
    assert body["fdr_number"] == draft["fdr_number"]  # fields not sent are untouched
    stored = row(engine, draft["id"])
    assert stored.row_version == 2 and stored.principal_amount == Decimal("2000000.50")
    assert len(history(engine, draft["id"])) == 1  # editing is not a status change
    (event,) = audit_events(engine, "fdr.update", "success")
    assert event.details == {"changed_fields": ["principal_amount", "purpose"]}
    assert "Updated purpose" not in json.dumps(event.details)


def test_edit_can_clear_optional_fields_but_not_required_ones(engine, world):
    owner = institution_user(engine, world)
    draft = create(owner, world)
    r = owner.patch(f"/fdrs/{draft['id']}", {"row_version": 1, "purpose": None, "interest_rate_pct": None})
    assert r.status_code == 200 and r.json()["purpose"] is None and r.json()["interest_rate_pct"] is None
    for field in ("fdr_number", "bank_id", "principal_amount", "issue_date", "maturity_date"):
        bad = owner.patch(f"/fdrs/{draft['id']}", {"row_version": 2, field: None})
        assert bad.status_code == 422, field


def test_changing_the_number_updates_the_duplicate_detection_key(engine, world):
    owner = institution_user(engine, world)
    draft = create(owner, world)
    owner.patch(f"/fdrs/{draft['id']}", {"row_version": 1, "fdr_number": " ab 12-c "})
    stored = row(engine, draft["id"])
    assert stored.fdr_number == "ab 12-c" and stored.fdr_number_normalized == "AB12-C"


def test_a_no_op_edit_changes_nothing_and_is_not_audited(engine, world):
    owner = institution_user(engine, world)
    draft = create(owner, world)
    r = owner.patch(f"/fdrs/{draft['id']}", {"row_version": 1, "purpose": draft["purpose"]})
    assert r.status_code == 200 and r.json()["row_version"] == 1
    assert audit_events(engine, "fdr.update") == []


@pytest.mark.parametrize("change", [
    {"principal_amount": "0"}, {"principal_amount": "1.234"}, {"interest_rate_pct": "101"},
    {"maturity_date": "2026-01-15"},   # equal to the stored issue date: merged values are validated
    {"issue_date": "2027-02-01"},       # after the stored maturity date
    {"bank_id": str(uuid.uuid4())}, {"fdr_number": "   "},
    {"status": "SUBMITTED"}, {"institution_id": str(uuid.uuid4())}, {"created_by": str(uuid.uuid4())},
])
def test_invalid_edits_are_rejected_and_leave_the_draft_unchanged(engine, world, change):
    owner = institution_user(engine, world)
    draft = create(owner, world)
    r = owner.patch(f"/fdrs/{draft['id']}", {"row_version": 1, **change})
    assert r.status_code == 422, r.text
    stored = row(engine, draft["id"])
    assert stored.row_version == 1 and stored.status == "DRAFT" and stored.maturity_date == date(2027, 1, 15)
    assert audit_events(engine, "fdr.update") == []


def test_a_stale_version_is_refused(engine, world):
    owner = institution_user(engine, world)
    draft = create(owner, world)
    assert owner.patch(f"/fdrs/{draft['id']}", {"row_version": 1, "purpose": "first"}).status_code == 200
    r = owner.patch(f"/fdrs/{draft['id']}", {"row_version": 1, "purpose": "second"})
    assert r.status_code == 409 and "changed since you loaded" in r.json()["detail"]
    assert row(engine, draft["id"]).purpose == "first"
    (event,) = audit_events(engine, "fdr.update", "denied")
    assert event.details["reason"] == "stale_row_version"


@pytest.mark.parametrize("status", ["SUBMITTED", "ACCEPTED", "REJECTED", "WITHDRAWN"])
def test_only_drafts_and_returned_fdrs_can_be_edited(engine, world, status):
    owner = institution_user(engine, world)
    draft = create(owner, world)
    force_status(engine, draft["id"], status)
    r = owner.patch(f"/fdrs/{draft['id']}", {"row_version": 1, "purpose": "tamper"})
    assert r.status_code == 409 and r.json() == {"detail": "Only draft FDRs, or FDRs returned for correction, can be edited"}
    stored = row(engine, draft["id"])
    assert stored.purpose == "Security deposit" and stored.row_version == 1 and stored.status == status
    (event,) = audit_events(engine, "fdr.update", "denied")
    assert event.details["reason"] == "invalid_status"


# ---- institution isolation --------------------------------------------------------------------
def test_other_institutions_drafts_are_invisible_and_untouchable(engine, world):
    a = institution_user(engine, world, "A")
    b = institution_user(engine, world, "B")
    draft = create(a, world)
    for call in (
        lambda: b.get(f"/fdrs/{draft['id']}"),
        lambda: b.patch(f"/fdrs/{draft['id']}", {"row_version": 1, "purpose": "hijack"}),
        lambda: b.post(f"/fdrs/{draft['id']}/submit", {"row_version": 1}),
    ):
        r = call()
        assert r.status_code == 404 and r.json() == {"detail": "FDR not found"}  # same as a missing record
    stored = row(engine, draft["id"])
    assert stored.purpose == "Security deposit" and stored.status == "DRAFT" and stored.row_version == 1
    assert len(audit_events(engine, "authz.scope_denied", "denied")) == 3
    assert b.get("/fdrs").json()["total"] == 0


def test_a_missing_record_looks_identical_to_a_foreign_one(engine, world):
    owner = institution_user(engine, world)
    for call in (lambda i: owner.patch(f"/fdrs/{i}", {"row_version": 1}), lambda i: owner.post(f"/fdrs/{i}/submit", {"row_version": 1})):
        r = call(uuid.uuid4())
        assert r.status_code == 404 and r.json() == {"detail": "FDR not found"}


# ---- submit ---------------------------------------------------------------------------------
def test_submitting_moves_a_draft_to_submitted_with_history_snapshot_and_audit(engine, world):
    owner = institution_user(engine, world)
    draft = create(owner, world)
    r = owner.post(f"/fdrs/{draft['id']}/submit", {"row_version": 1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "SUBMITTED" and body["row_version"] == 2 and body["submitted_at"] is not None

    stored = row(engine, draft["id"])
    assert stored.status == "SUBMITTED" and stored.submitted_by == owner.user_id
    first, second = history(engine, draft["id"])
    assert (second.from_status, second.to_status, second.actor_user_id, second.actor_role) == ("DRAFT", "SUBMITTED", owner.user_id, "INSTITUTION_USER")
    (revision,) = revisions(engine, draft["id"])
    assert revision.revision_no == 1 and revision.submitted_by == owner.user_id
    assert revision.snapshot["fdr_number"] == "WR-FDR-001"
    assert revision.snapshot["principal_amount"] == "1500000.00"  # exact decimal string, never a float
    assert revision.snapshot["institution_id"] == str(world["A"])
    (event,) = audit_events(engine, "fdr.submit", "success")
    assert event.details == {"changed_fields": ["status"]} and event.entity_id == draft["id"]
    with Session(engine) as s:
        assert verify_chain(s).ok


def test_a_submitted_fdr_becomes_visible_to_reviewers(engine, world):
    owner = institution_user(engine, world)
    draft = create(owner, world)
    owner.post(f"/fdrs/{draft['id']}/submit", {"row_version": 1})
    reviewer = sign_in(engine, role="AICTE_REVIEWER")
    r = reviewer.get(f"/fdrs/{draft['id']}")
    assert r.status_code == 200 and r.json()["status"] == "SUBMITTED"


def test_the_snapshot_reflects_edits_made_before_submission(engine, world):
    owner = institution_user(engine, world)
    draft = create(owner, world)
    owner.patch(f"/fdrs/{draft['id']}", {"row_version": 1, "principal_amount": "999.99"})
    owner.post(f"/fdrs/{draft['id']}/submit", {"row_version": 2})
    (revision,) = revisions(engine, draft["id"])
    assert revision.snapshot["principal_amount"] == "999.99"


def test_a_submitted_fdr_cannot_be_edited_or_resubmitted(engine, world):
    owner = institution_user(engine, world)
    draft = create(owner, world)
    assert owner.post(f"/fdrs/{draft['id']}/submit", {"row_version": 1}).status_code == 200
    again = owner.post(f"/fdrs/{draft['id']}/submit", {"row_version": 2})
    assert again.status_code == 409 and again.json() == {"detail": "Only draft FDRs, or FDRs returned for correction, can be submitted"}
    edit = owner.patch(f"/fdrs/{draft['id']}", {"row_version": 2, "purpose": "late change"})
    assert edit.status_code == 409
    assert len(revisions(engine, draft["id"])) == 1 and len(history(engine, draft["id"])) == 2
    assert row(engine, draft["id"]).purpose == "Security deposit"
    assert len(audit_events(engine, "fdr.submit", "success")) == 1


@pytest.mark.parametrize("status", ["ACCEPTED", "REJECTED", "WITHDRAWN"])
def test_no_other_status_can_be_submitted(engine, world, status):
    owner = institution_user(engine, world)
    draft = create(owner, world)
    force_status(engine, draft["id"], status)
    r = owner.post(f"/fdrs/{draft['id']}/submit", {"row_version": 1})
    assert r.status_code == 409
    assert row(engine, draft["id"]).status == status and revisions(engine, draft["id"]) == []


@pytest.mark.parametrize("missing", ["branch_name", "depositor_name"])
def test_required_fields_must_be_present_to_submit(engine, world, missing):
    owner = institution_user(engine, world)
    draft = create(owner, world, **{missing: ...})
    r = owner.post(f"/fdrs/{draft['id']}/submit", {"row_version": 1})
    assert r.status_code == 422 and missing in r.json()["detail"]
    assert row(engine, draft["id"]).status == "DRAFT" and revisions(engine, draft["id"]) == []


def test_submit_needs_the_current_version(engine, world):
    owner = institution_user(engine, world)
    draft = create(owner, world)
    r = owner.post(f"/fdrs/{draft['id']}/submit", {"row_version": 7})
    assert r.status_code == 409
    assert row(engine, draft["id"]).status == "DRAFT"
    (event,) = audit_events(engine, "fdr.submit", "denied")
    assert event.details["reason"] == "stale_row_version"


def test_the_client_cannot_choose_the_target_status(engine, world):
    owner = institution_user(engine, world)
    draft = create(owner, world)
    for extra in ({"status": "ACCEPTED"}, {"to_status": "ACCEPTED"}, {"institution_id": str(world["B"])}):
        r = owner.post(f"/fdrs/{draft['id']}/submit", {"row_version": 1, **extra})
        assert r.status_code == 422
    assert row(engine, draft["id"]).status == "DRAFT"


def test_submitting_is_refused_when_another_live_fdr_uses_the_same_number_at_the_bank(engine, world):
    a = institution_user(engine, world, "A")
    b = institution_user(engine, world, "B")
    first = create(b, world, fdr_number="DUP 100")
    assert b.post(f"/fdrs/{first['id']}/submit", {"row_version": 1}).status_code == 200
    second = create(a, world, fdr_number="dup100")  # same number once normalised, same bank
    r = a.post(f"/fdrs/{second['id']}/submit", {"row_version": 1})
    assert r.status_code == 409 and "already exists" in r.json()["detail"]
    stored = row(engine, second["id"])
    assert stored.status == "DRAFT" and stored.row_version == 1
    assert revisions(engine, second["id"]) == [] and len(history(engine, second["id"])) == 1
    (event,) = audit_events(engine, "fdr.submit", "denied")
    assert event.details["reason"] == "duplicate_active_fdr_number"
    # the same number at a different bank is a different instrument
    third = create(a, world, fdr_number="DUP 100", bank_id=world["bank2"])
    assert a.post(f"/fdrs/{third['id']}/submit", {"row_version": 1}).status_code == 200


# ---- authentication, authorisation, CSRF, Origin ------------------------------------------------
WRITES = {
    "create": ("POST", "/fdrs", lambda w, i: payload(w)),
    "edit": ("PATCH", "/fdrs/{id}", lambda w, i: {"row_version": 1, "purpose": "x"}),
    "submit": ("POST", "/fdrs/{id}/submit", lambda w, i: {"row_version": 1}),
}


@pytest.fixture
def a_draft(engine, world):
    owner = institution_user(engine, world, "A")
    return owner, create(owner, world)


@pytest.mark.parametrize("name", WRITES)
def test_unauthenticated_requests_are_rejected(engine, world, a_draft, name):
    _, draft = a_draft
    method, path, make = WRITES[name]
    client = build_client(engine)
    r = client.request(method, f"{API}{path.format(id=draft['id'])}", json=make(world, draft["id"]))
    assert r.status_code == 401
    assert count(engine, FDR) == 1


@pytest.mark.parametrize("name", WRITES)
@pytest.mark.parametrize("role", CROSS_INSTITUTION_ROLES)
def test_only_institution_users_may_write(engine, world, a_draft, name, role):
    _, draft = a_draft
    method, path, make = WRITES[name]
    actor = sign_in(engine, role=role)
    r = actor.client.request(method, f"{API}{path.format(id=draft['id'])}", json=make(world, draft["id"]), headers=actor.headers)
    assert r.status_code == 403 and r.json() == {"detail": "Forbidden"}
    assert count(engine, FDR) == 1 and row(engine, draft["id"]).row_version == 1
    assert len(audit_events(engine, "authz.denied", "denied")) >= 1


@pytest.mark.parametrize("name", WRITES)
def test_a_pending_password_change_blocks_writes(engine, world, a_draft, name):
    _, draft = a_draft
    method, path, make = WRITES[name]
    actor = sign_in(engine, role="INSTITUTION_USER", institution_id=world["A"], must_change_password=True)
    r = actor.client.request(method, f"{API}{path.format(id=draft['id'])}", json=make(world, draft["id"]), headers=actor.headers)
    assert r.status_code == 403 and r.json() == {"detail": "Password change required"}
    assert count(engine, FDR) == 1


@pytest.mark.parametrize("name", WRITES)
@pytest.mark.parametrize("headers", [{}, {"X-CSRF-Token": "wrong"}])
def test_a_missing_or_wrong_csrf_token_is_refused_and_audited(engine, world, a_draft, name, headers):
    owner, draft = a_draft
    method, path, make = WRITES[name]
    r = owner.client.request(method, f"{API}{path.format(id=draft['id'])}", json=make(world, draft["id"]), headers=headers)
    assert r.status_code == 403 and r.json() == {"detail": "CSRF validation failed"}
    assert count(engine, FDR) == 1 and row(engine, draft["id"]).row_version == 1
    assert len(audit_events(engine, "security.csrf_rejected", "denied")) == 1


@pytest.mark.parametrize("name", WRITES)
def test_an_untrusted_origin_is_refused_even_with_a_valid_token(engine, world, a_draft, name):
    owner, draft = a_draft
    method, path, make = WRITES[name]
    r = owner.client.request(method, f"{API}{path.format(id=draft['id'])}", json=make(world, draft["id"]),
                             headers={**owner.headers, "Origin": "http://evil.example"})
    assert r.status_code == 403 and r.json() == {"detail": "Untrusted request origin"}
    assert count(engine, FDR) == 1 and row(engine, draft["id"]).row_version == 1
    assert len(audit_events(engine, "security.origin_rejected", "denied")) == 1


def test_responses_are_not_cacheable(engine, world, a_draft):
    owner, draft = a_draft
    assert owner.post("/fdrs", payload(world)).headers["cache-control"] == "no-store"
    assert owner.patch(f"/fdrs/{draft['id']}", {"row_version": 1, "purpose": "y"}).headers["cache-control"] == "no-store"
    assert owner.post(f"/fdrs/{draft['id']}/submit", {"row_version": 2}).headers["cache-control"] == "no-store"


def test_the_audit_trail_never_contains_amounts_names_or_numbers(engine, world):
    owner = institution_user(engine, world)
    draft = create(owner, world, fdr_number="CONFIDENTIAL-7781", depositor_name="Confidential Depositor", principal_amount="424242.42")
    owner.patch(f"/fdrs/{draft['id']}", {"row_version": 1, "purpose": "Confidential purpose"})
    owner.post(f"/fdrs/{draft['id']}/submit", {"row_version": 2})
    dump = json.dumps([[str(getattr(e, c.name)) for c in AuditLog.__table__.columns] for e in audit_rows(engine)])
    for secret in ("CONFIDENTIAL-7781", "Confidential Depositor", "424242", "Confidential purpose"):
        assert secret not in dump


# ---- concurrency (PostgreSQL only: needs real row locks) ----------------------------------------
def run_together(calls):
    barrier = threading.Barrier(len(calls))
    results, errors = [None] * len(calls), []

    def worker(i):
        try:
            barrier.wait()
            results[i] = calls[i]()
        except Exception as exc:  # any exception is a failure of the safety property
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(calls))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not errors and not any(t.is_alive() for t in threads)
    return results


@pytest.fixture
def pg_world(pg_head):
    inst = create_institution(pg_head, "WR-PG", "Write PG Institution (Synthetic)")
    with Session(pg_head) as s:
        bank = Bank(code="WR-PGB", name="Write PG Bank (Synthetic)")
        s.add(bank)
        s.commit()
        bank_id = str(bank.id)
    return {"A": inst, "B": inst, "bank1": bank_id, "bank2": bank_id}


def test_simultaneous_submissions_succeed_exactly_once(pg_head, pg_world):
    for round_no in range(6):
        actors = [institution_user(pg_head, pg_world, email=f"conc{round_no}-{i}@example.invalid") for i in range(5)]
        draft = create(actors[0], pg_world, fdr_number=f"CONC-{round_no}")
        statuses = run_together([lambda a=a: a.post(f"/fdrs/{draft['id']}/submit", {"row_version": 1}).status_code for a in actors])
        assert sorted(statuses) == [200] + [409] * 4, statuses
        assert len(revisions(pg_head, draft["id"])) == 1
        assert [h.to_status for h in history(pg_head, draft["id"])] == ["DRAFT", "SUBMITTED"]
        assert row(pg_head, draft["id"]).row_version == 2
    with Session(pg_head) as s:
        assert verify_chain(s).ok


def test_simultaneous_edits_with_the_same_version_apply_exactly_once(pg_head, pg_world):
    actors = [institution_user(pg_head, pg_world, email=f"edit-{i}@example.invalid") for i in range(4)]
    draft = create(actors[0], pg_world)
    statuses = run_together([
        lambda a=a, i=i: a.patch(f"/fdrs/{draft['id']}", {"row_version": 1, "purpose": f"edit {i}"}).status_code
        for i, a in enumerate(actors)
    ])
    assert sorted(statuses) == [200, 409, 409, 409], statuses
    stored = row(pg_head, draft["id"])
    assert stored.row_version == 2 and stored.purpose.startswith("edit ")
    assert len(audit_events(pg_head, "fdr.update", "success")) == 1


def test_an_edit_racing_a_submission_cannot_slip_into_a_submitted_record(pg_head, pg_world):
    for round_no in range(6):
        editor, submitter = (institution_user(pg_head, pg_world, email=f"race{round_no}-{i}@example.invalid") for i in range(2))
        draft = create(editor, pg_world, fdr_number=f"RACE-{round_no}")
        edit_status, submit_status = run_together([
            lambda: editor.patch(f"/fdrs/{draft['id']}", {"row_version": 1, "purpose": "racing edit"}).status_code,
            lambda: submitter.post(f"/fdrs/{draft['id']}/submit", {"row_version": 1}).status_code,
        ])
        stored = row(pg_head, draft["id"])
        # Both carry version 1, so exactly one can win; the loser sees the winner's change.
        assert sorted([edit_status, submit_status]) == [200, 409], (edit_status, submit_status)
        if submit_status == 200:
            assert stored.status == "SUBMITTED" and stored.purpose == "Security deposit"
            (revision,) = revisions(pg_head, draft["id"])
            assert revision.snapshot["purpose"] == "Security deposit"
        else:
            assert stored.status == "DRAFT" and stored.purpose == "racing edit"
            assert revisions(pg_head, draft["id"]) == []
