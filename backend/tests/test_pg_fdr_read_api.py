"""PostgreSQL-only checks for the read-only FDR API (skipped unless TEST_DATABASE_URL is set).

SQLite stores NUMERIC as floating point, so exact-money guarantees can only be proven here.
"""

import re
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.api_support import sign_in
from tests.test_fdr_read_api import World, item_ids


@pytest.fixture
def world(pg_head):
    with pg_head.connect() as conn:  # belt and braces: the API tests below run on the *_test database
        assert conn.execute(text("SELECT current_database()")).scalar().endswith("_test")
    return World(pg_head)


@pytest.mark.parametrize(
    "principal,rate",
    [
        ("999999999999.99", "100.00"),  # the largest representable amounts
        ("0.01", "0.00"),
        ("0.10", "7.10"),  # classic float trouble: 0.1 / 7.1
        ("1250000.50", None),
        ("123456789012.34", "12.34"),
    ],
)
def test_money_is_returned_as_the_exact_decimal_string_that_was_stored(world, pg_head, principal, rate):
    world.add("inst_a", "bank1", "SUBMITTED", "EXACT-1", principal_amount=Decimal(principal),
              interest_rate_pct=None if rate is None else Decimal(rate))
    admin = sign_in(pg_head, role="ADMIN")
    item = admin.get("/fdrs").json()["items"][0]
    assert item["principal_amount"] == principal
    assert item["interest_rate_pct"] == rate
    raw = admin.get("/fdrs").text
    assert not re.search(r'"(principal_amount|interest_rate_pct)"\s*:\s*[-\d]', raw)  # always quoted, never a JSON number


def test_totals_and_pages_use_the_database_not_application_memory(world, pg_head):
    """Counts come from SQL COUNT with the visibility filter applied, so hidden rows never leak
    into totals even with many rows in other tenants."""
    for n in range(40):
        world.add("inst_b", "bank2", "SUBMITTED" if n % 2 else "DRAFT", f"BULK-B-{n:03d}")
    for n in range(3):
        world.add("inst_a", "bank1", "SUBMITTED", f"BULK-A-{n}")
    user = sign_in(pg_head, role="INSTITUTION_USER", institution_id=world.inst_a_id)
    body = user.get("/fdrs", params={"page_size": 100}).json()
    assert body["total"] == 3 and len(body["items"]) == 3
    reviewer = sign_in(pg_head, role="AICTE_REVIEWER")
    body = reviewer.get("/fdrs", params={"page_size": 100}).json()
    assert body["total"] == 23 and len(body["items"]) == 23  # 20 submitted in B + 3 in A; the 20 B drafts are hidden


def test_search_is_prefix_based_on_the_normalised_key_in_postgres(world, pg_head):
    world.add("inst_a", "bank1", "SUBMITTED", "ab 12-34")
    world.add("inst_a", "bank1", "SUBMITTED", "AB 99")
    admin = sign_in(pg_head, role="ADMIN")
    assert sorted(i["fdr_number"] for i in admin.get("/fdrs", params={"q": "aB1"}).json()["items"]) == ["ab 12-34"]
    assert admin.get("/fdrs", params={"q": "ab_"}).json()["total"] == 0  # '_' is literal, not a single-char wildcard
    assert admin.get("/fdrs", params={"q": "a%"}).json()["total"] == 0


def test_page_walk_over_a_larger_set_is_complete_and_ordered(world, pg_head):
    ids = {str(world.add("inst_a", "bank1", "ACCEPTED", f"WALK-{n:03d}")) for n in range(37)}
    admin = sign_in(pg_head, role="ADMIN")
    seen, page = [], 1
    while True:
        body = admin.get("/fdrs", params={"page": page, "page_size": 10}).json()
        seen += [i["id"] for i in body["items"]]
        if page >= body["total_pages"]:
            break
        page += 1
    assert body["total"] == 37 and body["total_pages"] == 4
    assert len(seen) == len(set(seen)) == 37 and set(seen) == ids
    assert item_ids(admin.get("/fdrs", params={"page": 1, "page_size": 10})) == seen[:10]
