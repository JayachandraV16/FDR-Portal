"""Server-side registry sorting and the recent-activity feed."""

import json

import pytest

from tests.api_support import API, build_client, sign_in
from tests.fdr_support import sqlite_decimal_warning
from tests.test_fdr_review_api import COMMENT, decide, submitted
from tests.test_fdr_write_api import create, institution_user, world  # noqa: F401

pytestmark = sqlite_decimal_warning


def numbers(response):
    assert response.status_code == 200, response.text
    return [i["fdr_number"] for i in response.json()["items"]]


@pytest.fixture
def fdrs(engine, world):
    owner = institution_user(engine, world, "A")
    for number, amount, issue, maturity in [
        ("C-300", "300.00", "2026-01-03", "2027-03-01"),
        ("A-100", "900.00", "2026-01-01", "2027-01-01"),
        ("B-200", "100.00", "2026-01-02", "2027-02-01"),
        ("D-400", "100.00", "2026-01-04", "2027-04-01"),  # same principal as B-200: ordering must still be stable
    ]:
        create(owner, world, fdr_number=number, principal_amount=amount, issue_date=issue, maturity_date=maturity)
    return owner


@pytest.mark.parametrize(
    ("sort", "order", "expected"),
    [
        ("number", None, ["A-100", "B-200", "C-300", "D-400"]),
        ("number", "desc", ["D-400", "C-300", "B-200", "A-100"]),
        ("maturity", None, ["A-100", "B-200", "C-300", "D-400"]),
        ("maturity", "desc", ["D-400", "C-300", "B-200", "A-100"]),
        ("issue", "asc", ["A-100", "B-200", "C-300", "D-400"]),
    ],
)
def test_the_registry_sorts_on_the_server_by_the_chosen_column(fdrs, sort, order, expected):
    query = f"/fdrs?sort={sort}" + (f"&order={order}" if order else "")
    assert numbers(fdrs.get(query)) == expected


@pytest.mark.parametrize(("order", "reverse"), [("asc", False), ("desc", True)])
def test_sorting_by_principal_orders_by_amount_and_keeps_tied_rows_together(fdrs, order, reverse):
    body = fdrs.get(f"/fdrs?sort=principal&order={order}").json()["items"]
    amounts = [i["principal_amount"] for i in body]
    assert amounts == sorted(amounts, key=float, reverse=reverse)
    assert {i["fdr_number"] for i in body if i["principal_amount"] == "100.00"} == {"B-200", "D-400"}  # a tie, in either order


def test_the_default_order_is_unchanged(fdrs):
    assert numbers(fdrs.get("/fdrs")) == numbers(fdrs.get("/fdrs?sort=created&order=desc"))


def test_sorted_pages_never_overlap_or_skip_even_when_values_tie(fdrs):
    seen = []
    for page in (1, 2, 3, 4):
        seen += numbers(fdrs.get(f"/fdrs?sort=principal&order=asc&page_size=1&page={page}"))
    assert sorted(seen) == ["A-100", "B-200", "C-300", "D-400"] and len(set(seen)) == 4
    assert seen == numbers(fdrs.get("/fdrs?sort=principal&order=asc&page_size=4"))  # the same order however it is paged


def test_sorting_composes_with_filters_and_scope(engine, world, fdrs):
    other = institution_user(engine, world, "B")
    create(other, world, fdr_number="Z-999", principal_amount="5.00")
    assert "Z-999" not in numbers(fdrs.get("/fdrs?sort=number&order=desc"))  # another institution's record never appears
    assert numbers(fdrs.get("/fdrs?sort=number&q=b")) == ["B-200"]
    assert fdrs.get("/fdrs?sort=principal&status=DRAFT").json()["total"] == 4


@pytest.mark.parametrize("query", ["sort=password", "sort=number;drop", "sort=fdr_number_normalized", "order=sideways", "sort=", "sort=NUMBER"])
def test_only_whitelisted_sort_values_are_accepted(fdrs, query):
    assert fdrs.get(f"/fdrs?{query}").status_code == 422


# ---- recent activity -------------------------------------------------------------------------------------------
def test_activity_lists_recent_status_changes_newest_first_without_comments_or_names(engine, world):
    owner, fdr = submitted(engine, world, None, fdr_number="ACT-1")
    reviewer = sign_in(engine, role="AICTE_REVIEWER")
    assert decide(reviewer, "request-correction", fdr).status_code == 200
    r = owner.get("/activity")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    items = r.json()["items"]
    assert [(i["from_status"], i["to_status"], i["actor_role"]) for i in items] == [
        ("SUBMITTED", "CORRECTION_REQUESTED", "AICTE_REVIEWER"), ("DRAFT", "SUBMITTED", "INSTITUTION_USER"), (None, "DRAFT", "INSTITUTION_USER")]
    assert items[0]["fdr_number"] == "ACT-1" and items[0]["institution_name"].startswith("Write Institution A")
    assert set(items[0]) == {"occurred_at", "fdr_id", "fdr_number", "institution_name", "from_status", "to_status", "actor_role"}
    assert COMMENT not in json.dumps(r.json()) and "@example.invalid" not in json.dumps(r.json())


def test_activity_is_scoped_like_the_registry(engine, world):
    a, b = institution_user(engine, world, "A"), institution_user(engine, world, "B")
    submitted(engine, world, a, fdr_number="ACT-A")
    submitted(engine, world, b, fdr_number="ACT-B")
    create(a, world, fdr_number="ACT-DRAFT")
    assert {i["fdr_number"] for i in a.get("/activity").json()["items"]} == {"ACT-A", "ACT-DRAFT"}
    assert {i["fdr_number"] for i in b.get("/activity").json()["items"]} == {"ACT-B"}
    reviewer = {i["fdr_number"] for i in sign_in(engine, role="AICTE_REVIEWER").get("/activity").json()["items"]}
    assert reviewer == {"ACT-A", "ACT-B"}  # a reviewer never sees a record that is still a draft, or its history


def test_activity_honours_its_limit_and_validates_it(engine, world):
    owner, _ = submitted(engine, world, None, fdr_number="ACT-L")
    assert len(owner.get("/activity?limit=2").json()["items"]) == 2
    assert owner.get("/activity?limit=0").status_code == 422 and owner.get("/activity?limit=51").status_code == 422


def test_activity_needs_a_session(engine, world):
    assert build_client(engine).get(API + "/activity").status_code == 401
    assert sign_in(engine, role="AICTE_REVIEWER", must_change_password=True).get("/activity").status_code == 403
