"""Database constraint behaviour for the FDR tables.

Runs on SQLite (structural stand-in) and on the dedicated PostgreSQL test database when
TEST_DATABASE_URL is set. Tests that depend on PostgreSQL-only behaviour (foreign-key enforcement,
exact NUMERIC handling, triggers) live in test_pg_fdr_constraints.py / test_pg_fdr_migration_0003.py.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import FDR, FDRRevision, FDRStatusHistory, FdrStatus
from tests.fdr_support import (
    add_fdr,
    add_history,
    add_revision,
    fdr_kwargs,
    rejected_by_database,
    seed_prereqs,
    sqlite_decimal_warning,
)

pytestmark = sqlite_decimal_warning


@pytest.fixture
def p(engine):
    return seed_prereqs(engine)


def insert_is_rejected(engine, p, **overrides) -> bool:
    return rejected_by_database(engine, lambda s: s.add(FDR(**fdr_kwargs(p, **overrides))))


def number(n: int) -> dict:
    return {"fdr_number": f"N-{n}", "fdr_number_normalized": f"N-{n}"}


# ---- defaults --------------------------------------------------------------------------------
def test_defaults_status_draft_row_version_one_and_timestamps(engine, p):
    fdr_id = add_fdr(engine, p)
    with Session(engine) as s:
        fdr = s.get(FDR, fdr_id)
        assert fdr.status == "DRAFT" and fdr.row_version == 1
        assert fdr.created_at is not None and fdr.updated_at is not None
        assert fdr.submitted_at is None and fdr.submitted_by is None and fdr.decided_at is None


def test_a_minimal_draft_without_optional_fields_is_accepted(engine, p):
    add_fdr(engine, p, interest_rate_pct=None, branch_name=None, depositor_name=None, purpose=None)


# ---- status ----------------------------------------------------------------------------------
@pytest.mark.parametrize("status", [s.value for s in FdrStatus])
def test_every_approved_status_is_accepted(engine, p, status):
    assert not insert_is_rejected(engine, p, status=status)


@pytest.mark.parametrize("status", ["", "draft", "Draft", "VERIFIED", "APPROVED", "UNDER_REVIEW", "x" * 31])
def test_unknown_status_values_are_rejected(engine, p, status):
    assert insert_is_rejected(engine, p, status=status)


def test_status_cannot_be_null(engine, p):
    """Core insert on purpose: the ORM would substitute the Python-side default for an explicit None."""
    from sqlalchemy import insert

    stmt = insert(FDR.__table__).values(id=uuid.uuid4(), **fdr_kwargs(p, status=None, row_version=1))
    assert rejected_by_database(engine, lambda s: s.execute(stmt))


# ---- principal amount ------------------------------------------------------------------------
@pytest.mark.parametrize("amount", ["0", "0.00", "-0.01", "-1000000"])
def test_principal_must_be_strictly_positive(engine, p, amount):
    assert insert_is_rejected(engine, p, principal_amount=Decimal(amount))


@pytest.mark.parametrize("amount", ["0.01", "1", "1250000.50"])
def test_positive_principal_is_accepted(engine, p, amount):
    assert not insert_is_rejected(engine, p, principal_amount=Decimal(amount))


def test_principal_is_required(engine, p):
    assert insert_is_rejected(engine, p, principal_amount=None)


# ---- interest rate ---------------------------------------------------------------------------
@pytest.mark.parametrize("rate", ["-0.01", "-5", "100.01", "250"])
def test_interest_rate_outside_zero_to_one_hundred_is_rejected(engine, p, rate):
    assert insert_is_rejected(engine, p, interest_rate_pct=Decimal(rate))


@pytest.mark.parametrize("rate", ["0", "0.01", "7.10", "100", None])
def test_interest_rate_bounds_are_inclusive_and_null_is_allowed(engine, p, rate):
    assert not insert_is_rejected(engine, p, interest_rate_pct=None if rate is None else Decimal(rate))


# ---- dates -----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "issue,maturity",
    [(date(2025, 6, 1), date(2025, 6, 1)), (date(2025, 6, 2), date(2025, 6, 1)), (date(2030, 1, 1), date(2020, 1, 1))],
)
def test_maturity_must_be_strictly_after_issue(engine, p, issue, maturity):
    assert insert_is_rejected(engine, p, issue_date=issue, maturity_date=maturity)


def test_past_maturity_dates_are_allowed(engine, p):
    assert not insert_is_rejected(engine, p, issue_date=date(2000, 1, 1), maturity_date=date(2001, 1, 1))


def test_one_day_tenure_is_allowed(engine, p):
    assert not insert_is_rejected(engine, p, issue_date=date(2025, 6, 1), maturity_date=date(2025, 6, 2))


@pytest.mark.parametrize(
    "issue,maturity",
    [
        (date(1975, 3, 1), date(1976, 3, 1)),  # historical instrument
        (date(1950, 1, 1), date(1960, 1, 1)),
        (date(1899, 12, 31), date(1900, 12, 31)),
        (date(1989, 12, 31), date(2000, 1, 1)),  # spans the former lower bound
        (date(2000, 1, 1), date(2101, 1, 1)),  # spans the former upper bound
        (date(2100, 12, 31), date(2150, 6, 30)),
        (date(202, 1, 1), date(2025, 1, 1)),  # nothing but ordering is enforced
    ],
)
def test_valid_dates_are_accepted_whatever_the_era_provided_maturity_is_later(engine, p, issue, maturity):
    """No arbitrary calendar window: the only date rule is maturity strictly after issue."""
    assert not insert_is_rejected(engine, p, issue_date=issue, maturity_date=maturity)


@pytest.mark.parametrize(
    "issue,maturity",
    [
        (date(1975, 5, 1), date(1975, 4, 30)),  # reversed, historical
        (date(1975, 5, 1), date(1975, 5, 1)),  # equal, historical
        (date(2150, 6, 30), date(2100, 1, 1)),  # reversed, far future
        (date(2150, 6, 30), date(2150, 6, 30)),
    ],
)
def test_date_ordering_is_enforced_outside_the_former_range_too(engine, p, issue, maturity):
    assert insert_is_rejected(engine, p, issue_date=issue, maturity_date=maturity)


def test_no_calendar_range_constraint_exists(engine, p):
    from app.models.fdr import FDR as Model

    names = {c.name for c in Model.__table__.constraints if c.name}
    assert "ck_fdrs_dates_in_range" not in names and "ck_fdrs_maturity_after_issue" in names


# ---- identifiers and version -----------------------------------------------------------------
def test_empty_instrument_numbers_are_rejected(engine, p):
    assert insert_is_rejected(engine, p, fdr_number="")
    assert insert_is_rejected(engine, p, fdr_number_normalized="")


@pytest.mark.parametrize("version", [0, -1])
def test_row_version_must_be_positive(engine, p, version):
    assert insert_is_rejected(engine, p, row_version=version)


def test_required_columns_cannot_be_null(engine, p):
    for column in ("institution_id", "bank_id", "fdr_number", "fdr_number_normalized", "issue_date",
                   "maturity_date", "created_by"):
        assert insert_is_rejected(engine, p, **{column: None}), column


# ---- partial unique index (global per bank, active states only) ------------------------------
ACTIVE = ["SUBMITTED", "CORRECTION_REQUESTED", "ACCEPTED"]
INACTIVE = ["DRAFT", "REJECTED", "WITHDRAWN"]


@pytest.mark.parametrize("first", ACTIVE)
@pytest.mark.parametrize("second", ACTIVE)
def test_two_active_records_cannot_share_a_normalized_number_at_one_bank(engine, p, first, second):
    add_fdr(engine, p, status=first)
    assert insert_is_rejected(engine, p, status=second, fdr_number="fdr  001")  # same normalized key


@pytest.mark.parametrize("inactive", INACTIVE)
@pytest.mark.parametrize("active", ACTIVE)
def test_inactive_records_neither_block_nor_are_blocked_by_active_ones(engine, p, inactive, active):
    add_fdr(engine, p, status=inactive)
    assert not insert_is_rejected(engine, p, status=active)
    assert not insert_is_rejected(engine, p, status=inactive)  # duplicates among inactive are fine too


def test_drafts_may_duplicate_each_other(engine, p):
    add_fdr(engine, p)
    add_fdr(engine, p)
    add_fdr(engine, p, institution_id=p.institution2_id)


def test_the_same_number_at_a_different_bank_is_allowed(engine, p):
    add_fdr(engine, p, status="ACCEPTED")
    assert not insert_is_rejected(engine, p, status="ACCEPTED", bank_id=p.bank2_id)


def test_uniqueness_is_global_across_institutions(engine, p):
    add_fdr(engine, p, status="SUBMITTED")
    assert insert_is_rejected(engine, p, status="SUBMITTED", institution_id=p.institution2_id)


def test_different_numbers_do_not_conflict(engine, p):
    add_fdr(engine, p, status="ACCEPTED", **number(1))
    assert not insert_is_rejected(engine, p, status="ACCEPTED", **number(2))


def test_promoting_a_duplicate_draft_into_an_active_state_is_blocked(engine, p):
    add_fdr(engine, p, status="ACCEPTED")
    draft = add_fdr(engine, p)  # allowed while DRAFT
    assert rejected_by_database(engine, lambda s: s.execute(update(FDR).where(FDR.id == draft).values(status="SUBMITTED")))
    with Session(engine) as s:
        assert s.get(FDR, draft).status == "DRAFT"


def test_rejection_or_withdrawal_frees_the_number_for_reuse(engine, p):
    first = add_fdr(engine, p, status="SUBMITTED")
    assert insert_is_rejected(engine, p, status="SUBMITTED")
    with Session(engine) as s:
        s.execute(update(FDR).where(FDR.id == first).values(status="REJECTED"))
        s.commit()
    assert not insert_is_rejected(engine, p, status="SUBMITTED")


def test_normalized_key_not_the_display_number_drives_uniqueness(engine, p):
    add_fdr(engine, p, status="ACCEPTED", fdr_number="ab 12", fdr_number_normalized="AB12")
    assert insert_is_rejected(engine, p, status="SUBMITTED", fdr_number="AB12", fdr_number_normalized="AB12")
    assert not insert_is_rejected(engine, p, status="SUBMITTED", fdr_number="AB-12", fdr_number_normalized="AB-12")


# ---- status history table --------------------------------------------------------------------
def test_history_accepts_a_creation_entry_and_a_transition(engine, p):
    fdr_id = add_fdr(engine, p)
    add_history(engine, p, fdr_id)
    add_history(engine, p, fdr_id, from_status="DRAFT", to_status="SUBMITTED", comment="ok")
    with Session(engine) as s:
        rows = s.scalars(select(FDRStatusHistory).order_by(FDRStatusHistory.id)).all()
        assert [r.to_status for r in rows] == ["DRAFT", "SUBMITTED"] and rows[1].id > rows[0].id
        assert rows[0].occurred_at is not None


@pytest.mark.parametrize(
    "overrides",
    [
        {"to_status": "VERIFIED"},
        {"from_status": "BOGUS", "to_status": "SUBMITTED"},
        {"from_status": "DRAFT", "to_status": "DRAFT"},  # not a change
        {"actor_role": "SUPERUSER"},
        {"comment": "x" * 2001},
        {"to_status": None},
        {"actor_role": None},
    ],
)
def test_history_constraints(engine, p, overrides):
    fdr_id = add_fdr(engine, p)
    assert rejected_by_database(engine, lambda s: s.add(FDRStatusHistory(
        **{"fdr_id": fdr_id, "from_status": None, "to_status": "DRAFT", "actor_user_id": p.user_id,
           "actor_role": "INSTITUTION_USER", **overrides})))


def test_history_comment_of_exactly_2000_characters_is_allowed(engine, p):
    add_history(engine, p, add_fdr(engine, p), comment="x" * 2000)


# ---- revisions table -------------------------------------------------------------------------
def test_revision_numbers_are_unique_per_fdr_but_not_across_fdrs(engine, p):
    first = add_fdr(engine, p, **number(1))
    second = add_fdr(engine, p, **number(2))
    add_revision(engine, p, first, 1)
    add_revision(engine, p, first, 2)
    add_revision(engine, p, second, 1)  # same number on a different FDR is fine
    assert rejected_by_database(engine, lambda s: s.add(FDRRevision(
        fdr_id=first, revision_no=2, snapshot={"a": "b"}, submitted_by=p.user_id)))


@pytest.mark.parametrize("n", [0, -1])
def test_revision_number_must_be_positive(engine, p, n):
    fdr_id = add_fdr(engine, p)
    assert rejected_by_database(engine, lambda s: s.add(FDRRevision(
        fdr_id=fdr_id, revision_no=n, snapshot={"a": "b"}, submitted_by=p.user_id)))


def test_snapshot_round_trips_with_money_as_strings(engine, p):
    fdr_id = add_fdr(engine, p)
    snapshot = {"principal_amount": "1250000.50", "interest_rate_pct": "7.10", "nested": {"k": ["1.00"]}}
    rev_id = add_revision(engine, p, fdr_id, 1, snapshot)
    with Session(engine) as s:
        assert s.get(FDRRevision, rev_id).snapshot == snapshot


def test_snapshot_must_be_a_json_object(engine, p):
    fdr_id = add_fdr(engine, p)
    for bad in (None, [], "text", 5):
        with pytest.raises(ValueError):
            FDRRevision(fdr_id=fdr_id, revision_no=1, snapshot=bad, submitted_by=p.user_id)


def test_a_missing_snapshot_is_a_sql_null_that_the_database_rejects(engine, p):
    """Bypasses the model validator to prove NOT NULL really protects the column (none_as_null)."""
    from sqlalchemy import insert

    fdr_id = add_fdr(engine, p)
    stmt = insert(FDRRevision.__table__).values(
        id=uuid.uuid4(), fdr_id=fdr_id, revision_no=1, snapshot=None, submitted_by=p.user_id
    )
    assert rejected_by_database(engine, lambda s: s.execute(stmt))
