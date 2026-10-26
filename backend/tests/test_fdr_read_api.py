"""Stage 5b: read-only FDR and bank endpoints.

Every test runs on SQLite and, when TEST_DATABASE_URL is set, on the dedicated PostgreSQL test
database (see the `engine` fixture). Money-exactness checks are PostgreSQL-only (test_pg_fdr_read_api.py).

Visibility policy under test (provisional, decision D4 pending; see app/security/scope.py):
  INSTITUTION_USER -> own institution, every status.
  REVIEWER / APPROVER / ADMIN / AUDITOR -> all institutions, submitted-state statuses only
  (SUBMITTED, CORRECTION_REQUESTED, ACCEPTED, REJECTED); DRAFT and WITHDRAWN are hidden.
"""

import json
import math
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FDR, AuditLog, Bank, FdrStatus, FDRRevision, FDRStatusHistory, Institution, User
from app.models.fdr import normalize_fdr_number
from tests.api_support import (
    API,
    audit_events,
    audit_rows,
    build_client,
    create_user,
    sign_in,
)
from tests.fdr_support import sqlite_decimal_warning

pytestmark = sqlite_decimal_warning

ALL_STATUSES = [s.value for s in FdrStatus]
SUBMITTED_STATES = {"SUBMITTED", "CORRECTION_REQUESTED", "ACCEPTED", "REJECTED"}
CROSS_INSTITUTION_ROLES = ["AICTE_REVIEWER", "AICTE_APPROVER", "ADMIN", "AUDITOR"]
FDR_KEYS = {
    "id", "institution", "bank", "fdr_number", "branch_name", "depositor_name", "purpose", "currency",
    "principal_amount", "interest_rate_pct", "issue_date", "maturity_date", "status", "row_version",
    "created_at", "updated_at", "submitted_at", "decided_at",
}


# ---- data builders (synthetic only) ----------------------------------------------------------
class World:
    def __init__(self, engine):
        self.engine = engine
        with Session(engine) as s:
            self.inst_a = Institution(code="RD-A", name="Read Institution A (Synthetic)", state="X", institution_type="Engineering")
            self.inst_b = Institution(code="RD-B", name="Read Institution B (Synthetic)", state="Y", institution_type="Polytechnic")
            self.bank1 = Bank(code="RD-B1", name="Alpha Sample Bank (Synthetic)")
            self.bank2 = Bank(code="RD-B2", name="Beta Sample Bank (Synthetic)")
            s.add_all([self.inst_a, self.inst_b, self.bank1, self.bank2])
            s.commit()
            self.ids = {k: getattr(self, k).id for k in ("inst_a", "inst_b", "bank1", "bank2")}
        self.creator = create_user(engine, email="creator@example.invalid", role="ADMIN")
        self.fdrs: dict[tuple[str, str], uuid.UUID] = {}

    def __getattr__(self, name):  # a.id -> convenient ids without detached-instance access
        if name in ("inst_a_id", "inst_b_id", "bank1_id", "bank2_id"):
            return self.ids[name[:-3]]
        raise AttributeError(name)

    def add(self, inst: str, bank: str, status: str, number: str, **kw) -> uuid.UUID:
        values = dict(
            institution_id=self.ids[inst], bank_id=self.ids[bank], fdr_number=number,
            fdr_number_normalized=normalize_fdr_number(number), principal_amount=Decimal("100000.00"),
            interest_rate_pct=Decimal("7.10"), issue_date=date(2025, 1, 1), maturity_date=date(2026, 1, 1),
            status=status, created_by=self.creator,
        )
        values.update(kw)
        with Session(self.engine) as s:
            fdr = FDR(**values)
            s.add(fdr)
            s.commit()
            return fdr.id

    def full_matrix(self):
        """One FDR per (institution, status): 12 rows. Institution A uses bank1, B uses bank2."""
        for inst, bank in (("inst_a", "bank1"), ("inst_b", "bank2")):
            for status in ALL_STATUSES:
                self.fdrs[(inst, status)] = self.add(inst, bank, status, f"{inst}-{status}-001")
        return self


@pytest.fixture
def world(engine):
    return World(engine)


@pytest.fixture
def matrix(world):
    return world.full_matrix()


def actor_for(engine, role, world):
    return sign_in(engine, role=role, institution_id=world.inst_a_id if role == "INSTITUTION_USER" else None)


def item_ids(response) -> list[str]:
    return [i["id"] for i in response.json()["items"]]


def expected_ids(world, role) -> set[str]:
    if role == "INSTITUTION_USER":  # signed in as a member of institution A
        return {str(world.fdrs[("inst_a", st)]) for st in ALL_STATUSES}
    return {str(v) for (inst, st), v in world.fdrs.items() if st in SUBMITTED_STATES}


# ---- 1. authenticated listing and response shape ---------------------------------------------
def test_list_returns_the_documented_shape_and_no_internal_fields(engine, matrix):
    admin = actor_for(engine, "ADMIN", matrix)
    r = admin.get("/fdrs")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert set(body) == {"items", "page", "page_size", "total", "total_pages"}
    assert (body["page"], body["page_size"], body["total"], body["total_pages"]) == (1, 25, 8, 1)
    item = body["items"][0]
    assert set(item) == FDR_KEYS
    assert set(item["bank"]) == {"id", "code", "name"} and set(item["institution"]) == {"id", "code", "name"}
    assert item["currency"] == "INR"
    assert isinstance(item["principal_amount"], str) and item["principal_amount"] == "100000.00"
    assert item["interest_rate_pct"] == "7.10"
    text = json.dumps(body).lower()
    for forbidden in ("created_by", "submitted_by", "password", "token", "hash", "snapshot", "audit"):
        assert forbidden not in text


def test_money_is_serialised_as_json_strings_not_floats(engine, world):
    world.add("inst_a", "bank1", "SUBMITTED", "MONEY-1", principal_amount=Decimal("1250000.50"), interest_rate_pct=None)
    admin = actor_for(engine, "ADMIN", world)
    raw = admin.get("/fdrs").text
    assert '"principal_amount":"1250000.50"' in raw.replace(" ", "")
    assert '"interest_rate_pct":null' in raw.replace(" ", "")


# ---- 2. single retrieval ---------------------------------------------------------------------
@pytest.mark.parametrize("role", CROSS_INSTITUTION_ROLES)
def test_cross_institution_roles_can_fetch_a_submitted_fdr_in_any_institution(engine, matrix, role):
    actor = actor_for(engine, role, matrix)
    for inst in ("inst_a", "inst_b"):
        fdr_id = matrix.fdrs[(inst, "SUBMITTED")]
        r = actor.get(f"/fdrs/{fdr_id}")
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        assert r.json()["id"] == str(fdr_id) and set(r.json()) == FDR_KEYS
        assert r.json() in [i for i in actor.get("/fdrs").json()["items"]]  # detail equals list view


def test_institution_user_can_fetch_every_status_of_their_own_institution(engine, matrix):
    user = actor_for(engine, "INSTITUTION_USER", matrix)
    for status in ALL_STATUSES:
        r = user.get(f"/fdrs/{matrix.fdrs[('inst_a', status)]}")
        assert r.status_code == 200 and r.json()["status"] == status


# ---- 3. unauthenticated ----------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/fdrs", "/fdrs/{id}", "/banks"])
def test_unauthenticated_requests_get_401(engine, matrix, path):
    client = build_client(engine)
    url = f"{API}{path.format(id=matrix.fdrs[('inst_a', 'SUBMITTED')])}"
    r = client.get(url)
    assert r.status_code == 401 and r.json() == {"detail": "Not authenticated"}
    client.cookies.set("fdr_session", "forged-or-expired")
    assert client.get(url).status_code == 401


def test_deactivated_and_pending_password_change_users_are_rejected(engine, matrix):
    from sqlalchemy import update

    user = actor_for(engine, "AICTE_REVIEWER", matrix)
    assert user.get("/fdrs").status_code == 200
    with Session(engine) as s:
        s.execute(update(User).where(User.id == user.user_id).values(must_change_password=True))
        s.commit()
    for path in ("/fdrs", f"/fdrs/{matrix.fdrs[('inst_a', 'SUBMITTED')]}", "/banks"):
        r = user.get(path)
        assert r.status_code == 403 and r.json() == {"detail": "Password change required"}
    with Session(engine) as s:
        s.execute(update(User).where(User.id == user.user_id).values(must_change_password=False, is_active=False))
        s.commit()
    assert user.get("/fdrs").status_code == 401


# ---- 4/5. institution isolation and bypass attempts ------------------------------------------
def test_institution_user_sees_only_their_own_institution_and_only_their_own_counts(engine, matrix):
    user = actor_for(engine, "INSTITUTION_USER", matrix)
    body = user.get("/fdrs", params={"page_size": 100}).json()
    assert set(item_ids(user.get("/fdrs", params={"page_size": 100}))) == expected_ids(matrix, "INSTITUTION_USER")
    assert body["total"] == 6 and {i["institution"]["code"] for i in body["items"]} == {"RD-A"}


def test_institution_user_gets_the_same_404_for_other_tenants_and_nonexistent_records(engine, matrix):
    user = actor_for(engine, "INSTITUTION_USER", matrix)
    other = user.get(f"/fdrs/{matrix.fdrs[('inst_b', 'SUBMITTED')]}")
    other_draft = user.get(f"/fdrs/{matrix.fdrs[('inst_b', 'DRAFT')]}")
    missing = user.get(f"/fdrs/{uuid.uuid4()}")
    assert other.status_code == other_draft.status_code == missing.status_code == 404
    assert other.json() == other_draft.json() == missing.json() == {"detail": "FDR not found"}
    denied = audit_events(engine, "authz.scope_denied", "denied")
    assert len(denied) == 3 and all(e.actor_user_id == user.user_id for e in denied)
    assert denied[0].details["target_fdr_id"] == str(matrix.fdrs[("inst_b", "SUBMITTED")])
    assert denied[0].details["route"] == "/api/v1/fdrs/{fdr_id}"
    assert all(e.entity_id is None for e in denied)


@pytest.mark.parametrize(
    "params,headers",
    [
        ({"institution_id": "INST_B"}, {}),
        ({"institution_id": "INST_B", "bank_id": "BANK2"}, {}),
        ({"bank_id": "BANK2"}, {}),
        ({"q": "inst_b-SUBMITTED-001"}, {}),
        ({"status": "DRAFT", "institution_id": "INST_B"}, {}),
        ({"page": 2, "page_size": 3, "institution_id": "INST_B"}, {}),
        ({"page_size": 100, "role": "ADMIN", "scope": "all", "tenant": "INST_B"}, {}),
        ({}, {"X-Institution-Id": "INST_B", "X-Role": "ADMIN", "X-Forwarded-User": "admin"}),
    ],
)
def test_filters_headers_and_pagination_cannot_escape_the_tenant_scope(engine, matrix, params, headers):
    user = actor_for(engine, "INSTITUTION_USER", matrix)
    subs = {"INST_B": str(matrix.inst_b_id), "BANK2": str(matrix.bank2_id)}
    params = {k: subs.get(v, v) if isinstance(v, str) else v for k, v in params.items()}
    headers = {k: subs.get(v, v) for k, v in headers.items()}
    own = {str(v) for (inst, _), v in matrix.fdrs.items() if inst == "inst_a"}
    r = user.client.get(f"{API}/fdrs", params={**params, "page_size": params.get("page_size", 100)}, headers=headers)
    assert r.status_code == 200
    assert set(item_ids(r)) <= own
    assert r.json()["total"] <= 6  # never counts other tenants


def test_the_institution_filter_is_ignored_for_institution_users_but_honoured_for_cross_institution_roles(engine, matrix):
    user = actor_for(engine, "INSTITUTION_USER", matrix)
    spoof = user.get("/fdrs", params={"institution_id": str(matrix.inst_b_id), "page_size": 100}).json()
    assert spoof["total"] == 6 and {i["institution"]["code"] for i in spoof["items"]} == {"RD-A"}
    admin = actor_for(engine, "ADMIN", matrix)
    only_b = admin.get("/fdrs", params={"institution_id": str(matrix.inst_b_id), "page_size": 100}).json()
    assert only_b["total"] == 4 and {i["institution"]["code"] for i in only_b["items"]} == {"RD-B"}


def test_scope_follows_the_current_database_row(engine, matrix):
    from sqlalchemy import update

    user = actor_for(engine, "INSTITUTION_USER", matrix)
    b_fdr = matrix.fdrs[("inst_b", "DRAFT")]
    assert user.get(f"/fdrs/{b_fdr}").status_code == 404
    with Session(engine) as s:  # an administrator moves the user to institution B mid-session
        s.execute(update(User).where(User.id == user.user_id).values(institution_id=matrix.inst_b_id))
        s.commit()
    assert user.get(f"/fdrs/{b_fdr}").status_code == 200
    assert user.get(f"/fdrs/{matrix.fdrs[('inst_a', 'DRAFT')]}").status_code == 404
    assert user.get("/fdrs", params={"page_size": 100}).json()["total"] == 6


# ---- 6. role-by-role visibility --------------------------------------------------------------
@pytest.mark.parametrize("role", ["INSTITUTION_USER", *CROSS_INSTITUTION_ROLES])
def test_visible_set_per_role(engine, matrix, role):
    actor = actor_for(engine, role, matrix)
    r = actor.get("/fdrs", params={"page_size": 100})
    assert set(item_ids(r)) == expected_ids(matrix, role)
    assert r.json()["total"] == len(expected_ids(matrix, role))


@pytest.mark.parametrize("role", CROSS_INSTITUTION_ROLES)
@pytest.mark.parametrize("hidden", ["DRAFT", "WITHDRAWN"])
def test_draft_and_withdrawn_records_are_hidden_from_cross_institution_roles(engine, matrix, role, hidden):
    actor = actor_for(engine, role, matrix)
    for inst in ("inst_a", "inst_b"):
        r = actor.get(f"/fdrs/{matrix.fdrs[(inst, hidden)]}")
        assert r.status_code == 404 and r.json() == {"detail": "FDR not found"}
    assert audit_events(engine, "authz.scope_denied") == []  # not a tenant violation, so not logged


@pytest.mark.parametrize("role", CROSS_INSTITUTION_ROLES)
def test_hidden_statuses_cannot_be_reached_through_the_status_filter(engine, matrix, role):
    actor = actor_for(engine, role, matrix)
    for hidden in ("DRAFT", "WITHDRAWN"):
        body = actor.get("/fdrs", params={"status": hidden}).json()
        assert body["items"] == [] and body["total"] == 0 and body["total_pages"] == 0


# ---- 7. validation ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "params",
    [
        {"page": 0}, {"page": -1}, {"page": "abc"}, {"page": 100_001}, {"page": 1.5},
        {"page_size": 0}, {"page_size": 101}, {"page_size": -5}, {"page_size": "abc"},
        {"status": "BOGUS"}, {"status": "draft"}, {"status": "VERIFIED"},
        {"bank_id": "not-a-uuid"}, {"institution_id": "123"},
        {"q": ""}, {"q": "x" * 65}, {"q": "   "}, {"q": "\t\n"},
    ],
)
def test_invalid_query_parameters_are_rejected_with_422(engine, matrix, params):
    admin = actor_for(engine, "ADMIN", matrix)
    assert admin.get("/fdrs", params=params).status_code == 422


def test_invalid_parameters_are_rejected_for_institution_users_too(engine, matrix):
    user = actor_for(engine, "INSTITUTION_USER", matrix)
    assert user.get("/fdrs", params={"institution_id": "nope"}).status_code == 422
    assert user.get("/fdrs", params={"page_size": 1000}).status_code == 422


def test_boundary_values_are_accepted(engine, matrix):
    admin = actor_for(engine, "ADMIN", matrix)
    assert admin.get("/fdrs", params={"page": 1, "page_size": 1}).status_code == 200
    assert admin.get("/fdrs", params={"page": 100_000, "page_size": 100}).status_code == 200
    assert admin.get("/fdrs", params={"q": "x" * 64}).status_code == 200


# ---- 8. deterministic pagination and accurate totals -----------------------------------------
def test_pagination_is_deterministic_and_gap_free_even_when_timestamps_tie(engine, world):
    tie = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    created = {world.add("inst_a", "bank1", "SUBMITTED", f"TIE-{n}", created_at=tie, updated_at=tie) for n in range(7)}
    admin = actor_for(engine, "ADMIN", world)
    pages = [admin.get("/fdrs", params={"page": p, "page_size": 3}).json() for p in (1, 2, 3, 4)]
    assert [len(p["items"]) for p in pages] == [3, 3, 1, 0]
    assert all(p["total"] == 7 and p["total_pages"] == 3 and p["page_size"] == 3 for p in pages)
    seen = [i["id"] for p in pages for i in p["items"]]
    assert len(seen) == len(set(seen)) == 7 and set(seen) == {str(i) for i in created}  # no overlap, no gaps
    assert seen == sorted(seen, reverse=True)  # unique id breaks the created_at tie, descending
    again = [i["id"] for p in (1, 2, 3) for i in admin.get("/fdrs", params={"page": p, "page_size": 3}).json()["items"]]
    assert again == seen  # stable across repeated requests


def test_newest_first_ordering(engine, world):
    base = datetime(2026, 1, 1, tzinfo=UTC)
    ids = [world.add("inst_a", "bank1", "SUBMITTED", f"ORD-{n}", created_at=base + timedelta(days=n)) for n in range(4)]
    admin = actor_for(engine, "ADMIN", world)
    assert item_ids(admin.get("/fdrs")) == [str(i) for i in reversed(ids)]


def test_totals_are_exact_for_every_page_size_and_never_count_hidden_rows(engine, matrix):
    reviewer = actor_for(engine, "AICTE_REVIEWER", matrix)  # sees 8 of 12
    for size in (1, 2, 3, 7, 8, 9, 100):
        body = reviewer.get("/fdrs", params={"page_size": size}).json()
        assert body["total"] == 8 and body["total_pages"] == math.ceil(8 / size)
    walked = []
    for p in range(1, 9):
        walked += item_ids(reviewer.get("/fdrs", params={"page": p, "page_size": 1}))
    assert len(walked) == len(set(walked)) == 8


# ---- 9. status and bank filters --------------------------------------------------------------
@pytest.mark.parametrize("status", sorted(SUBMITTED_STATES))
def test_status_filter(engine, matrix, status):
    admin = actor_for(engine, "ADMIN", matrix)
    body = admin.get("/fdrs", params={"status": status}).json()
    assert body["total"] == 2 and {i["status"] for i in body["items"]} == {status}


def test_institution_user_status_filter_includes_their_drafts(engine, matrix):
    user = actor_for(engine, "INSTITUTION_USER", matrix)
    body = user.get("/fdrs", params={"status": "DRAFT"}).json()
    assert body["total"] == 1 and body["items"][0]["institution"]["code"] == "RD-A"


def test_bank_filter_and_combined_filters(engine, matrix):
    admin = actor_for(engine, "ADMIN", matrix)
    by_bank = admin.get("/fdrs", params={"bank_id": str(matrix.bank2_id)}).json()
    assert by_bank["total"] == 4 and {i["bank"]["code"] for i in by_bank["items"]} == {"RD-B2"}
    combo = admin.get("/fdrs", params={"bank_id": str(matrix.bank2_id), "status": "ACCEPTED"}).json()
    assert combo["total"] == 1 and combo["items"][0]["status"] == "ACCEPTED"
    none = admin.get("/fdrs", params={"bank_id": str(matrix.bank2_id), "institution_id": str(matrix.inst_a_id)}).json()
    assert none["total"] == 0 and none["items"] == []
    assert admin.get("/fdrs", params={"bank_id": str(uuid.uuid4())}).json()["total"] == 0


# ---- 10. search normalisation ----------------------------------------------------------------
@pytest.fixture
def searchable(world):
    world.add("inst_a", "bank1", "SUBMITTED", "FDR 001")
    world.add("inst_a", "bank1", "SUBMITTED", "fdr 002")
    world.add("inst_a", "bank1", "ACCEPTED", "Fdr-003")
    world.add("inst_b", "bank2", "SUBMITTED", "FDR 004")
    world.add("inst_a", "bank1", "SUBMITTED", "XY_100%")
    return world


def numbers(response) -> list[str]:
    return sorted(i["fdr_number"] for i in response.json()["items"])


@pytest.mark.parametrize("q", ["fdr", "FDR", "  fdr  ", "f d r", "Fdr"])
def test_search_ignores_case_and_whitespace(engine, searchable, q):
    admin = actor_for(engine, "ADMIN", searchable)
    assert numbers(admin.get("/fdrs", params={"q": q})) == ["FDR 001", "FDR 004", "Fdr-003", "fdr 002"]


def test_search_matches_the_normalised_prefix_only(engine, searchable):
    admin = actor_for(engine, "ADMIN", searchable)
    assert numbers(admin.get("/fdrs", params={"q": "fdr001"})) == ["FDR 001"]
    assert numbers(admin.get("/fdrs", params={"q": "FDR 0"})) == ["FDR 001", "FDR 004", "fdr 002"]
    assert numbers(admin.get("/fdrs", params={"q": "FDR-0"})) == ["Fdr-003"]  # punctuation is significant
    assert admin.get("/fdrs", params={"q": "001"}).json()["total"] == 0  # prefix, not substring


@pytest.mark.parametrize(
    "q,expected",
    [
        ("%", []), ("_", []), ("F%", []), ("F_R", []), ("\\", []), ("%FDR", []),  # wildcards match nothing special
        ("XY_1%", []),  # a literal '%' right after "XY_1" does not occur in "XY_100%"
        ("XY_10", ["XY_100%"]), ("xy_100%", ["XY_100%"]),  # literal '_' and '%' do match themselves
        ("XY%", []), ("X_", []),
    ],
)
def test_like_wildcards_are_treated_literally(engine, searchable, q, expected):
    admin = actor_for(engine, "ADMIN", searchable)
    assert numbers(admin.get("/fdrs", params={"q": q})) == expected


def test_search_composes_with_filters_and_stays_inside_the_tenant_scope(engine, searchable):
    user = actor_for(engine, "INSTITUTION_USER", searchable)
    assert user.get("/fdrs", params={"q": "FDR 004"}).json()["total"] == 0  # B's number is invisible
    assert numbers(user.get("/fdrs", params={"q": "fdr", "status": "ACCEPTED"})) == ["Fdr-003"]
    admin = actor_for(engine, "ADMIN", searchable)
    assert numbers(admin.get("/fdrs", params={"q": "fdr", "institution_id": str(searchable.inst_b_id)})) == ["FDR 004"]


# ---- 11. banks -------------------------------------------------------------------------------
@pytest.mark.parametrize("role", ["INSTITUTION_USER", *CROSS_INSTITUTION_ROLES])
def test_every_authenticated_role_can_read_the_bank_list(engine, world, role):
    actor = actor_for(engine, role, world)
    r = actor.get("/banks")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert [b["code"] for b in body["items"]] == ["RD-B1", "RD-B2"]  # ordered by name
    assert all(set(b) == {"id", "code", "name"} for b in body["items"])
    assert (body["page"], body["page_size"], body["total"], body["total_pages"]) == (1, 100, 2, 1)


def test_bank_list_pagination_and_validation(engine, world):
    admin = actor_for(engine, "ADMIN", world)
    assert [b["code"] for b in admin.get("/banks", params={"page": 2, "page_size": 1}).json()["items"]] == ["RD-B2"]
    assert admin.get("/banks", params={"page": 3, "page_size": 1}).json()["items"] == []
    for params in ({"page": 0}, {"page_size": 0}, {"page_size": 501}, {"page": "x"}):
        assert admin.get("/banks", params=params).status_code == 422


def test_banks_are_read_only(engine, world):
    admin = actor_for(engine, "ADMIN", world)
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert admin.client.request(method, f"{API}/banks", json={"code": "X", "name": "Y"}, headers=admin.headers).status_code == 405
    with Session(engine) as s:
        assert {b.code for b in s.scalars(select(Bank))} == {"RD-B1", "RD-B2"}


# ---- 12. empty results and nonexistent ids ---------------------------------------------------
def test_empty_results_have_consistent_metadata(engine, world):
    for role in ("ADMIN", "INSTITUTION_USER"):
        actor = actor_for(engine, role, world)
        body = actor.get("/fdrs").json()
        assert body == {"items": [], "page": 1, "page_size": 25, "total": 0, "total_pages": 0}


@pytest.mark.parametrize("role", ["INSTITUTION_USER", *CROSS_INSTITUTION_ROLES])
def test_unknown_ids_return_404_and_malformed_ids_422(engine, matrix, role):
    actor = actor_for(engine, role, matrix)
    r = actor.get(f"/fdrs/{uuid.uuid4()}")
    assert r.status_code == 404 and r.json() == {"detail": "FDR not found"}
    for bad in ("not-a-uuid", "123", "%00"):
        assert actor.get(f"/fdrs/{bad}").status_code == 422


def test_page_beyond_the_last_is_empty_but_keeps_the_true_total(engine, matrix):
    admin = actor_for(engine, "ADMIN", matrix)
    body = admin.get("/fdrs", params={"page": 50, "page_size": 5}).json()
    assert body["items"] == [] and body["total"] == 8 and body["total_pages"] == 2


# ---- 13. GET never mutates -------------------------------------------------------------------
def table_state(engine):
    with Session(engine) as s:
        def rows(model, order):
            return [{c.name: getattr(r, c.name) for c in model.__table__.columns} for r in s.scalars(select(model).order_by(order))]
        return {
            "fdrs": rows(FDR, FDR.id),
            "history": rows(FDRStatusHistory, FDRStatusHistory.id),
            "revisions": rows(FDRRevision, FDRRevision.id),
            "audit": rows(AuditLog, AuditLog.seq),
            "banks": rows(Bank, Bank.id),
            "institutions": rows(Institution, Institution.id),
        }


@pytest.mark.parametrize("role", ["INSTITUTION_USER", *CROSS_INSTITUTION_ROLES])
def test_successful_reads_change_nothing(engine, matrix, role):
    actor = actor_for(engine, role, matrix)
    before = table_state(engine)
    for _ in range(2):
        actor.get("/fdrs", params={"page_size": 100})
        actor.get("/fdrs", params={"status": "SUBMITTED", "q": "inst"})
        actor.get("/banks")
        for fdr_id in list(matrix.fdrs.values())[:4]:
            actor.get(f"/fdrs/{fdr_id}")
    assert table_state(engine) == before  # FDR rows, row_version, history, revisions, audit all identical


def test_a_denied_read_adds_exactly_one_audit_event_and_changes_nothing_else(engine, matrix):
    user = actor_for(engine, "INSTITUTION_USER", matrix)
    before = table_state(engine)
    assert user.get(f"/fdrs/{matrix.fdrs[('inst_b', 'SUBMITTED')]}").status_code == 404
    after = table_state(engine)
    assert {k: v for k, v in after.items() if k != "audit"} == {k: v for k, v in before.items() if k != "audit"}
    assert len(after["audit"]) == len(before["audit"]) + 1 and after["audit"][-1]["action"] == "authz.scope_denied"


def test_row_versions_are_untouched_by_reads(engine, matrix):
    admin = actor_for(engine, "ADMIN", matrix)
    admin.get("/fdrs", params={"page_size": 100})
    with Session(engine) as s:
        assert {f.row_version for f in s.scalars(select(FDR))} == {1}


# ---- 14. cache and write-method hygiene ------------------------------------------------------
def test_no_write_methods_exist_on_fdr_routes(engine, matrix):
    admin = actor_for(engine, "ADMIN", matrix)
    one = matrix.fdrs[("inst_a", "SUBMITTED")]
    for method, path in (("POST", "/fdrs"), ("PUT", f"/fdrs/{one}"), ("PATCH", f"/fdrs/{one}"), ("DELETE", f"/fdrs/{one}"),
                         ("POST", f"/fdrs/{one}/submit"), ("POST", f"/fdrs/{one}/accept")):
        r = admin.client.request(method, f"{API}{path}", json={}, headers=admin.headers)
        assert r.status_code in (404, 405), (method, path, r.status_code)
    assert audit_rows(engine) == [] or all(not e.action.startswith("fdr.") for e in audit_rows(engine))
