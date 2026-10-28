"""AICTE review workflow: reviewer queue, review decisions, correction requests and resubmission.

State machine under test (see app/api/review.py):
    SUBMITTED -> CORRECTION_REQUESTED  (REVIEWER or APPROVER, comment required)
    SUBMITTED -> ACCEPTED              (APPROVER only, comment optional)
    SUBMITTED -> REJECTED              (APPROVER only, comment required)
    CORRECTION_REQUESTED -> SUBMITTED  (the institution resubmits, optionally after editing)

Runs on SQLite and, when TEST_DATABASE_URL is set, on the dedicated PostgreSQL test database. The
concurrency tests need real row locks and are PostgreSQL-only. All data is synthetic.
"""

import json
import uuid

import pytest
from sqlalchemy.orm import Session

from app.audit import verify_chain
from app.models import FDR, AuditLog
from tests.api_support import API, audit_events, audit_rows, build_client, sign_in
from tests.fdr_support import sqlite_decimal_warning
from tests.test_fdr_write_api import (  # noqa: F401  (fixtures are used by name)
    count,
    create,
    force_status,
    history,
    institution_user,
    payload,
    pg_world,
    revisions,
    row,
    run_together,
    world,
)

pytestmark = sqlite_decimal_warning

ACTIONS = ["request-correction", "accept", "reject"]
TARGET = {"request-correction": "CORRECTION_REQUESTED", "accept": "ACCEPTED", "reject": "REJECTED"}
# Who may perform each decision. Everyone else, including ADMIN, must be refused.
ALLOWED = {
    "request-correction": {"AICTE_REVIEWER", "AICTE_APPROVER"},
    "accept": {"AICTE_APPROVER"},
    "reject": {"AICTE_APPROVER"},
}
ALL_ROLES = ["INSTITUTION_USER", "AICTE_REVIEWER", "AICTE_APPROVER", "ADMIN", "AUDITOR"]
COMMENT = "Please attach the bank's FDR advice and re-enter the maturity date."


def actor(engine, world, role):
    if role == "INSTITUTION_USER":
        return institution_user(engine, world, "A")
    return sign_in(engine, role=role)


def submitted(engine, world, owner=None, **overrides):
    """A record the institution has really submitted (through the API, not by editing rows)."""
    owner = owner or institution_user(engine, world, "A")
    draft = create(owner, world, **overrides)
    r = owner.post(f"/fdrs/{draft['id']}/submit", {"row_version": draft["row_version"]})
    assert r.status_code == 200, r.text
    return owner, r.json()


def decide(who, action, fdr, **body):
    body = {"row_version": fdr["row_version"], **body}
    if action in ("request-correction", "reject"):
        body.setdefault("comment", COMMENT)
    return who.post(f"/fdrs/{fdr['id']}/{action}", body)


def events(engine, *prefixes):
    return [e for e in audit_rows(engine) if e.action.startswith(prefixes)]


# ---- reviewer queue ----------------------------------------------------------------------------
@pytest.mark.parametrize("role", ["AICTE_REVIEWER", "AICTE_APPROVER"])
def test_the_queue_lists_only_submitted_records_oldest_first(engine, world, role):
    a = institution_user(engine, world, "A")
    b = institution_user(engine, world, "B")
    _, first = submitted(engine, world, a, fdr_number="Q-1")
    _, second = submitted(engine, world, b, fdr_number="Q-2")
    _, third = submitted(engine, world, a, fdr_number="Q-3")
    create(a, world, fdr_number="Q-DRAFT")  # never visible to AICTE
    _, returned = submitted(engine, world, a, fdr_number="Q-RETURNED")
    assert decide(sign_in(engine, role="AICTE_REVIEWER"), "request-correction", returned).status_code == 200
    _, accepted = submitted(engine, world, a, fdr_number="Q-ACCEPTED")
    assert decide(sign_in(engine, role="AICTE_APPROVER"), "accept", accepted).status_code == 200

    reviewer = actor(engine, world, role)
    body = reviewer.get("/review/queue").json()
    assert [i["fdr_number"] for i in body["items"]] == ["Q-1", "Q-2", "Q-3"]  # first in, first out
    assert body["total"] == 3 and body["total_pages"] == 1
    assert {i["status"] for i in body["items"]} == {"SUBMITTED"}
    assert {i["institution"]["code"] for i in body["items"]} == {"WR-A", "WR-B"}  # all institutions
    assert set(body["items"][0]) == {  # the registry fields, plus the document-check summary and nothing else
        "id", "institution", "bank", "fdr_number", "branch_name", "depositor_name", "purpose", "currency",
        "principal_amount", "interest_rate_pct", "issue_date", "maturity_date", "status", "row_version",
        "created_at", "updated_at", "submitted_at", "decided_at", "document_check",
    }


def test_the_queue_supports_filters_and_server_side_pagination(engine, world):
    a = institution_user(engine, world, "A")
    b = institution_user(engine, world, "B")
    submitted(engine, world, a, fdr_number="AA-1")
    submitted(engine, world, a, fdr_number="AA-2", bank_id=world["bank2"])
    submitted(engine, world, b, fdr_number="BB-1")
    reviewer = sign_in(engine, role="AICTE_REVIEWER")
    assert reviewer.get("/review/queue", params={"q": "aa"}).json()["total"] == 2
    assert reviewer.get("/review/queue", params={"bank_id": world["bank2"]}).json()["total"] == 1
    assert reviewer.get("/review/queue", params={"institution_id": str(world["B"])}).json()["total"] == 1
    page = reviewer.get("/review/queue", params={"page_size": 2}).json()
    assert (page["total"], page["total_pages"], len(page["items"])) == (3, 2, 2)
    assert len(reviewer.get("/review/queue", params={"page_size": 2, "page": 2}).json()["items"]) == 1
    assert reviewer.get("/review/queue", params={"q": "   "}).status_code == 422


@pytest.mark.parametrize("role", ["INSTITUTION_USER", "ADMIN", "AUDITOR"])
def test_the_queue_is_closed_to_other_roles(engine, world, role):
    submitted(engine, world)
    r = actor(engine, world, role).get("/review/queue")
    assert r.status_code == 403 and r.json() == {"detail": "Forbidden"}
    assert len(audit_events(engine, "authz.denied", "denied")) == 1


def test_the_queue_requires_a_session(engine, world):
    assert build_client(engine).get(f"{API}/review/queue").status_code == 401


# ---- who may decide ------------------------------------------------------------------------------
@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("role", ALL_ROLES)
def test_the_role_matrix_for_review_decisions(engine, world, action, role):
    _, fdr = submitted(engine, world)
    who = actor(engine, world, role)
    r = decide(who, action, fdr)
    stored = row(engine, fdr["id"])
    if role in ALLOWED[action]:
        assert r.status_code == 200, r.text
        assert r.json()["status"] == TARGET[action] and stored.status == TARGET[action]
    else:
        assert r.status_code == 403 and r.json() == {"detail": "Forbidden"}
        assert (stored.status, stored.row_version) == ("SUBMITTED", fdr["row_version"])
        assert len(history(engine, fdr["id"])) == 2  # DRAFT, SUBMITTED: nothing was added
        assert not events(engine, "fdr.request_correction", "fdr.accept", "fdr.reject")
        assert len(audit_events(engine, "authz.denied", "denied")) >= 1


def test_admin_has_no_review_power_even_though_it_can_read(engine, world):
    _, fdr = submitted(engine, world)
    admin = sign_in(engine, role="ADMIN")
    assert admin.get(f"/fdrs/{fdr['id']}").status_code == 200  # can read...
    for action in ACTIONS:
        assert decide(admin, action, fdr).status_code == 403  # ...cannot decide
    assert row(engine, fdr["id"]).status == "SUBMITTED"


# ---- request correction ----------------------------------------------------------------------------
def test_a_correction_request_records_status_history_and_audit(engine, world):
    owner, fdr = submitted(engine, world)
    reviewer = sign_in(engine, role="AICTE_REVIEWER")
    r = decide(reviewer, "request-correction", fdr, comment=f"  {COMMENT}  ")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "CORRECTION_REQUESTED" and body["row_version"] == fdr["row_version"] + 1
    assert body["decided_at"] is None  # a correction request is not a decision

    stored = row(engine, fdr["id"])
    assert stored.status == "CORRECTION_REQUESTED" and stored.decided_at is None
    entry = history(engine, fdr["id"])[-1]
    assert (entry.from_status, entry.to_status, entry.actor_user_id, entry.actor_role, entry.comment) == (
        "SUBMITTED", "CORRECTION_REQUESTED", reviewer.user_id, "AICTE_REVIEWER", COMMENT)
    (event,) = audit_events(engine, "fdr.request_correction", "success")
    assert (event.actor_user_id, event.actor_role, event.entity_id) == (reviewer.user_id, "AICTE_REVIEWER", fdr["id"])
    assert event.details == {"changed_fields": ["status"]}
    with Session(engine) as s:
        assert verify_chain(s).ok


@pytest.mark.parametrize("comment", [..., None, "", "   \n\t ", "x" * 2001])
def test_a_correction_request_needs_a_comment(engine, world, comment):
    _, fdr = submitted(engine, world)
    reviewer = sign_in(engine, role="AICTE_REVIEWER")
    body = {"row_version": fdr["row_version"]}
    if comment is not ...:
        body["comment"] = comment
    r = reviewer.post(f"/fdrs/{fdr['id']}/request-correction", body)
    assert r.status_code == 422
    assert row(engine, fdr["id"]).status == "SUBMITTED" and len(history(engine, fdr["id"])) == 2
    assert not events(engine, "fdr.request_correction")


def test_a_comment_of_the_maximum_length_is_accepted(engine, world):
    _, fdr = submitted(engine, world)
    reviewer = sign_in(engine, role="AICTE_REVIEWER")
    assert decide(reviewer, "request-correction", fdr, comment="x" * 2000).status_code == 200
    assert history(engine, fdr["id"])[-1].comment == "x" * 2000


def test_the_institution_can_read_the_reason(engine, world):
    owner, fdr = submitted(engine, world)
    decide(sign_in(engine, role="AICTE_REVIEWER"), "request-correction", fdr)
    body = owner.get(f"/fdrs/{fdr['id']}/history").json()
    last = body["history"][-1]
    assert last["to_status"] == "CORRECTION_REQUESTED" and last["comment"] == COMMENT
    assert last["actor_role"] == "AICTE_REVIEWER"
    assert owner.get(f"/fdrs/{fdr['id']}").json()["status"] == "CORRECTION_REQUESTED"


def test_the_institution_corrects_and_resubmits_keeping_every_earlier_revision(engine, world):
    owner, fdr = submitted(engine, world, purpose="Original purpose")
    reviewer = sign_in(engine, role="AICTE_REVIEWER")
    returned = decide(reviewer, "request-correction", fdr).json()

    edited = owner.patch(f"/fdrs/{fdr['id']}", {"row_version": returned["row_version"], "purpose": "Corrected purpose"})
    assert edited.status_code == 200 and edited.json()["status"] == "CORRECTION_REQUESTED"
    resubmitted = owner.post(f"/fdrs/{fdr['id']}/submit", {"row_version": edited.json()["row_version"]})
    assert resubmitted.status_code == 200 and resubmitted.json()["status"] == "SUBMITTED"

    first, second = revisions(engine, fdr["id"])
    first, second = sorted([first, second], key=lambda r: r.revision_no)
    assert (first.revision_no, second.revision_no) == (1, 2)
    assert first.snapshot["purpose"] == "Original purpose"  # the earlier submission is preserved untouched
    assert second.snapshot["purpose"] == "Corrected purpose"
    assert [(h.from_status, h.to_status) for h in history(engine, fdr["id"])] == [
        (None, "DRAFT"), ("DRAFT", "SUBMITTED"), ("SUBMITTED", "CORRECTION_REQUESTED"), ("CORRECTION_REQUESTED", "SUBMITTED"),
    ]
    (event,) = audit_events(engine, "fdr.resubmit", "success")
    assert event.details == {"changed_fields": ["status"]}
    assert len(audit_events(engine, "fdr.submit", "success")) == 1  # only the first submission
    queue = reviewer.get("/review/queue").json()
    assert [i["id"] for i in queue["items"]] == [fdr["id"]]  # back in front of reviewers
    revs = owner.get(f"/fdrs/{fdr['id']}/history").json()["revisions"]
    assert [r["revision_no"] for r in revs] == [1, 2]


def test_a_returned_record_may_be_resubmitted_without_changes(engine, world):
    owner, fdr = submitted(engine, world)
    returned = decide(sign_in(engine, role="AICTE_REVIEWER"), "request-correction", fdr).json()
    r = owner.post(f"/fdrs/{fdr['id']}/submit", {"row_version": returned["row_version"]})
    assert r.status_code == 200 and len(revisions(engine, fdr["id"])) == 2


def test_resubmission_still_needs_the_required_details(engine, world):
    owner, fdr = submitted(engine, world)
    returned = decide(sign_in(engine, role="AICTE_REVIEWER"), "request-correction", fdr).json()
    edited = owner.patch(f"/fdrs/{fdr['id']}", {"row_version": returned["row_version"], "branch_name": None}).json()
    r = owner.post(f"/fdrs/{fdr['id']}/submit", {"row_version": edited["row_version"]})
    assert r.status_code == 422 and "branch_name" in r.json()["detail"]
    assert row(engine, fdr["id"]).status == "CORRECTION_REQUESTED"


def test_another_institution_cannot_touch_a_returned_record(engine, world):
    _, fdr = submitted(engine, world)
    returned = decide(sign_in(engine, role="AICTE_REVIEWER"), "request-correction", fdr).json()
    other = institution_user(engine, world, "B")
    for r in (other.patch(f"/fdrs/{fdr['id']}", {"row_version": returned["row_version"], "purpose": "x"}),
              other.post(f"/fdrs/{fdr['id']}/submit", {"row_version": returned["row_version"]}),
              other.get(f"/fdrs/{fdr['id']}/history")):
        assert r.status_code == 404 and r.json() == {"detail": "FDR not found"}
    assert row(engine, fdr["id"]).status == "CORRECTION_REQUESTED"


# ---- accept / reject ----------------------------------------------------------------------------
@pytest.mark.parametrize("action,status", [("accept", "ACCEPTED"), ("reject", "REJECTED")])
def test_a_final_decision_is_recorded_and_terminal(engine, world, action, status):
    owner, fdr = submitted(engine, world)
    approver = sign_in(engine, role="AICTE_APPROVER")
    r = decide(approver, action, fdr)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == status and body["decided_at"] is not None and body["row_version"] == fdr["row_version"] + 1
    stored = row(engine, fdr["id"])
    assert stored.status == status and stored.decided_at is not None
    entry = history(engine, fdr["id"])[-1]
    assert (entry.from_status, entry.to_status, entry.actor_user_id, entry.actor_role) == (
        "SUBMITTED", status, approver.user_id, "AICTE_APPROVER")
    (event,) = audit_events(engine, f"fdr.{action}", "success")
    assert (event.actor_user_id, event.entity_id, event.details) == (approver.user_id, fdr["id"], {"changed_fields": ["status"]})

    # Nothing can move it again.
    for again in ACTIONS:
        r2 = decide(approver, again, {**fdr, "row_version": body["row_version"]})
        assert r2.status_code == 409 and r2.json() == {"detail": "This FDR is not awaiting review"}, again
    assert owner.patch(f"/fdrs/{fdr['id']}", {"row_version": body["row_version"], "purpose": "x"}).status_code == 409
    assert owner.post(f"/fdrs/{fdr['id']}/submit", {"row_version": body["row_version"]}).status_code == 409
    final = row(engine, fdr["id"])
    assert final.status == status and final.row_version == body["row_version"]
    assert len(history(engine, fdr["id"])) == 3


def test_accepting_may_carry_an_optional_comment(engine, world):
    _, fdr = submitted(engine, world)
    approver = sign_in(engine, role="AICTE_APPROVER")
    assert decide(approver, "accept", fdr, comment="  Verified against the submitted advice.  ").status_code == 200
    assert history(engine, fdr["id"])[-1].comment == "Verified against the submitted advice."
    _, other = submitted(engine, world, fdr_number="ACC-2")
    assert decide(approver, "accept", other, comment="   ").status_code == 200
    assert history(engine, other["id"])[-1].comment is None


@pytest.mark.parametrize("comment", [..., None, "", "  ", "y" * 2001])
def test_a_rejection_needs_a_reason(engine, world, comment):
    _, fdr = submitted(engine, world)
    approver = sign_in(engine, role="AICTE_APPROVER")
    body = {"row_version": fdr["row_version"]}
    if comment is not ...:
        body["comment"] = comment
    assert approver.post(f"/fdrs/{fdr['id']}/reject", body).status_code == 422
    assert row(engine, fdr["id"]).status == "SUBMITTED" and not events(engine, "fdr.reject")


def test_the_institution_sees_the_rejection_reason(engine, world):
    owner, fdr = submitted(engine, world)
    decide(sign_in(engine, role="AICTE_APPROVER"), "reject", fdr, comment="Amount does not match the advice.")
    last = owner.get(f"/fdrs/{fdr['id']}/history").json()["history"][-1]
    assert (last["to_status"], last["comment"], last["actor_role"]) == ("REJECTED", "Amount does not match the advice.", "AICTE_APPROVER")


def test_a_reviewer_can_never_make_the_final_decision(engine, world):
    """Separation of duties: the reviewer can send a record back but never accept or reject it."""
    _, fdr = submitted(engine, world)
    reviewer = sign_in(engine, role="AICTE_REVIEWER")
    assert decide(reviewer, "accept", fdr).status_code == 403
    assert decide(reviewer, "reject", fdr).status_code == 403
    assert row(engine, fdr["id"]).status == "SUBMITTED"


# ---- invalid transitions, stale and hidden records -----------------------------------------------
@pytest.mark.parametrize("status", ["CORRECTION_REQUESTED", "ACCEPTED", "REJECTED"])
@pytest.mark.parametrize("action", ACTIONS)
def test_only_a_submitted_record_can_be_decided(engine, world, action, status):
    _, fdr = submitted(engine, world)
    force_status(engine, fdr["id"], status)
    r = decide(sign_in(engine, role="AICTE_APPROVER"), action, fdr)
    assert r.status_code == 409 and r.json() == {"detail": "This FDR is not awaiting review"}
    assert row(engine, fdr["id"]).status == status
    assert audit_events(engine, f"fdr.{action.replace('-', '_')}", "denied")[0].details["reason"] == "invalid_status"


@pytest.mark.parametrize("status", ["DRAFT", "WITHDRAWN"])
@pytest.mark.parametrize("action", ACTIONS)
def test_drafts_and_withdrawn_records_are_invisible_to_reviewers(engine, world, action, status):
    owner = institution_user(engine, world, "A")
    draft = create(owner, world)
    force_status(engine, draft["id"], status)
    r = decide(sign_in(engine, role="AICTE_APPROVER"), action, draft)
    assert r.status_code == 404 and r.json() == {"detail": "FDR not found"}
    assert row(engine, draft["id"]).status == status
    assert audit_events(engine, "authz.scope_denied", "denied")


def test_a_missing_record_is_a_404(engine, world):
    approver = sign_in(engine, role="AICTE_APPROVER")
    for action in ACTIONS:
        r = approver.post(f"/fdrs/{uuid.uuid4()}/{action}", {"row_version": 1, "comment": COMMENT})
        assert r.status_code == 404


@pytest.mark.parametrize("action", ACTIONS)
def test_a_stale_version_is_refused(engine, world, action):
    owner, fdr = submitted(engine, world)
    stale = {**fdr, "row_version": fdr["row_version"] - 1}
    r = decide(sign_in(engine, role="AICTE_APPROVER"), action, stale)
    assert r.status_code == 409 and "changed since you loaded" in r.json()["detail"]
    stored = row(engine, fdr["id"])
    assert stored.status == "SUBMITTED" and stored.row_version == fdr["row_version"]
    (event,) = audit_events(engine, f"fdr.{action.replace('-', '_')}", "denied")
    assert event.details["reason"] == "stale_row_version"


def test_two_sequential_decisions_cannot_both_win(engine, world):
    _, fdr = submitted(engine, world)
    first, second = sign_in(engine, role="AICTE_APPROVER"), sign_in(engine, role="AICTE_APPROVER")
    assert decide(first, "accept", fdr).status_code == 200
    r = decide(second, "reject", fdr)  # still holding the version it loaded
    assert r.status_code == 409
    assert row(engine, fdr["id"]).status == "ACCEPTED"
    assert len(history(engine, fdr["id"])) == 3


@pytest.mark.parametrize("extra", [{"status": "ACCEPTED"}, {"to_status": "ACCEPTED"}, {"institution_id": str(uuid.uuid4())}, {"decided_at": "2026-01-01T00:00:00Z"}])
@pytest.mark.parametrize("action", ACTIONS)
def test_decisions_accept_no_server_controlled_fields(engine, world, action, extra):
    _, fdr = submitted(engine, world)
    r = decide(sign_in(engine, role="AICTE_APPROVER"), action, fdr, **extra)
    assert r.status_code == 422 and row(engine, fdr["id"]).status == "SUBMITTED"


# ---- history ---------------------------------------------------------------------------------------
def test_history_shows_roles_and_comments_but_no_identities(engine, world):
    owner, fdr = submitted(engine, world)
    decide(sign_in(engine, role="AICTE_REVIEWER"), "request-correction", fdr)
    body = owner.get(f"/fdrs/{fdr['id']}/history")
    assert body.headers["cache-control"] == "no-store"
    data = body.json()
    assert [(h["from_status"], h["to_status"], h["actor_role"]) for h in data["history"]] == [
        (None, "DRAFT", "INSTITUTION_USER"), ("DRAFT", "SUBMITTED", "INSTITUTION_USER"),
        ("SUBMITTED", "CORRECTION_REQUESTED", "AICTE_REVIEWER"),
    ]
    assert set(data["history"][0]) == {"from_status", "to_status", "actor_role", "comment", "occurred_at"}
    assert set(data["revisions"][0]) == {"revision_no", "submitted_at"}  # the frozen snapshot is not exposed


@pytest.mark.parametrize("role", ["AICTE_REVIEWER", "AICTE_APPROVER", "ADMIN", "AUDITOR"])
def test_history_is_readable_by_every_role_that_can_read_the_record(engine, world, role):
    _, fdr = submitted(engine, world)
    assert actor(engine, world, role).get(f"/fdrs/{fdr['id']}/history").status_code == 200


def test_history_of_a_draft_is_hidden_from_everyone_but_its_institution(engine, world):
    owner = institution_user(engine, world, "A")
    draft = create(owner, world)
    assert owner.get(f"/fdrs/{draft['id']}/history").status_code == 200
    assert sign_in(engine, role="AUDITOR").get(f"/fdrs/{draft['id']}/history").status_code == 404
    other = institution_user(engine, world, "B")
    assert other.get(f"/fdrs/{draft['id']}/history").json() == {"detail": "FDR not found"}
    assert len(audit_events(engine, "authz.scope_denied", "denied")) == 1  # the institution user's attempt
    assert build_client(engine).get(f"{API}/fdrs/{draft['id']}/history").status_code == 401


# ---- session, Origin, CSRF ---------------------------------------------------------------------------
@pytest.mark.parametrize("action", ACTIONS)
def test_unauthenticated_decisions_are_rejected(engine, world, action):
    _, fdr = submitted(engine, world)
    r = build_client(engine).post(f"{API}/fdrs/{fdr['id']}/{action}", json={"row_version": 2, "comment": COMMENT})
    assert r.status_code == 401 and row(engine, fdr["id"]).status == "SUBMITTED"


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("headers", [{}, {"X-CSRF-Token": "wrong"}])
def test_a_missing_or_wrong_csrf_token_is_refused(engine, world, action, headers):
    _, fdr = submitted(engine, world)
    approver = sign_in(engine, role="AICTE_APPROVER")
    r = approver.client.post(f"{API}/fdrs/{fdr['id']}/{action}", json={"row_version": fdr["row_version"], "comment": COMMENT}, headers=headers)
    assert r.status_code == 403 and r.json() == {"detail": "CSRF validation failed"}
    assert row(engine, fdr["id"]).status == "SUBMITTED"
    assert len(audit_events(engine, "security.csrf_rejected", "denied")) == 1


@pytest.mark.parametrize("action", ACTIONS)
def test_an_untrusted_origin_is_refused(engine, world, action):
    _, fdr = submitted(engine, world)
    approver = sign_in(engine, role="AICTE_APPROVER")
    r = approver.client.post(f"{API}/fdrs/{fdr['id']}/{action}", json={"row_version": fdr["row_version"], "comment": COMMENT},
                             headers={**approver.headers, "Origin": "http://evil.example"})
    assert r.status_code == 403 and r.json() == {"detail": "Untrusted request origin"}
    assert row(engine, fdr["id"]).status == "SUBMITTED"
    assert len(audit_events(engine, "security.origin_rejected", "denied")) == 1


@pytest.mark.parametrize("action", ACTIONS)
def test_a_pending_password_change_blocks_decisions(engine, world, action):
    _, fdr = submitted(engine, world)
    pending = sign_in(engine, role="AICTE_APPROVER", must_change_password=True)
    r = decide(pending, action, fdr)
    assert r.status_code == 403 and r.json() == {"detail": "Password change required"}
    assert row(engine, fdr["id"]).status == "SUBMITTED"


def test_review_comments_never_reach_the_audit_trail(engine, world):
    owner, fdr = submitted(engine, world)
    secret = "Confidential reviewer remark 7731"
    returned = decide(sign_in(engine, role="AICTE_REVIEWER"), "request-correction", fdr, comment=secret).json()
    resub = owner.post(f"/fdrs/{fdr['id']}/submit", {"row_version": returned["row_version"]}).json()
    decide(sign_in(engine, role="AICTE_APPROVER"), "reject", resub, comment=secret + " again")
    dump = json.dumps([[str(getattr(e, c.name)) for c in AuditLog.__table__.columns] for e in audit_rows(engine)])
    assert "7731" not in dump
    with Session(engine) as s:
        assert verify_chain(s).ok
    assert count(engine, FDR) == 1


# ---- concurrency (PostgreSQL only: needs real row locks) ----------------------------------------------
def test_simultaneous_accept_and_reject_cannot_both_succeed(pg_head, pg_world):
    for round_no in range(6):
        _, fdr = submitted(pg_head, pg_world, fdr_number=f"RACE-AR-{round_no}")
        approvers = [sign_in(pg_head, role="AICTE_APPROVER") for _ in range(2)]
        statuses = run_together([
            lambda: decide(approvers[0], "accept", fdr).status_code,
            lambda: decide(approvers[1], "reject", fdr).status_code,
        ])
        assert sorted(statuses) == [200, 409], statuses
        stored = row(pg_head, fdr["id"])
        winner = "ACCEPTED" if statuses[0] == 200 else "REJECTED"
        assert stored.status == winner and stored.row_version == fdr["row_version"] + 1
        decisions = [h for h in history(pg_head, fdr["id"]) if h.to_status in ("ACCEPTED", "REJECTED")]
        assert [h.to_status for h in decisions] == [winner]
    assert len(events(pg_head, "fdr.accept", "fdr.reject")) >= 6
    with Session(pg_head) as s:
        assert verify_chain(s).ok


def test_simultaneous_identical_decisions_apply_once(pg_head, pg_world):
    _, fdr = submitted(pg_head, pg_world, fdr_number="RACE-SAME")
    reviewers = [sign_in(pg_head, role="AICTE_REVIEWER") for _ in range(4)]
    statuses = run_together([lambda r=r: decide(r, "request-correction", fdr).status_code for r in reviewers])
    assert sorted(statuses) == [200, 409, 409, 409], statuses
    assert [h.to_status for h in history(pg_head, fdr["id"])] == ["DRAFT", "SUBMITTED", "CORRECTION_REQUESTED"]
    assert len(audit_events(pg_head, "fdr.request_correction", "success")) == 1


def test_a_decision_racing_a_resubmission_cannot_produce_contradictory_state(pg_head, pg_world):
    for round_no in range(5):
        owner, fdr = submitted(pg_head, pg_world, fdr_number=f"RACE-RS-{round_no}")
        returned = decide(sign_in(pg_head, role="AICTE_REVIEWER"), "request-correction", fdr).json()
        approver = sign_in(pg_head, role="AICTE_APPROVER")
        submit_status, accept_status = run_together([
            lambda: owner.post(f"/fdrs/{fdr['id']}/submit", {"row_version": returned["row_version"]}).status_code,
            lambda: decide(approver, "accept", returned).status_code,  # not SUBMITTED yet, or stale: must fail
        ])
        stored = row(pg_head, fdr["id"])
        assert submit_status in (200, 409) and accept_status in (409,), (submit_status, accept_status)
        assert stored.status in ("SUBMITTED", "CORRECTION_REQUESTED")
        assert stored.decided_at is None  # nobody decided anything
