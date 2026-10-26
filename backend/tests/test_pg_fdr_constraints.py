"""PostgreSQL-only FDR schema behaviour (skipped unless TEST_DATABASE_URL is set).

These need a real server: foreign-key enforcement, exact NUMERIC semantics, JSONB, catalog checks.
Target is always the verified *_test database (see pg_support.assert_safe_test_database).
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.models import FDR, FDRRevision, FDRStatusHistory
from tests.fdr_support import add_fdr, add_history, add_revision, fdr_kwargs, seed_prereqs


@pytest.fixture
def p(pg_head):
    return seed_prereqs(pg_head)


def _fails(engine, build, exc=IntegrityError) -> bool:
    with Session(engine) as s:
        try:
            build(s)
            s.commit()
        except exc:
            s.rollback()
            return True
    return False


# ---- foreign keys (SQLite does not enforce them by default, so PostgreSQL only) ---------------
@pytest.mark.parametrize("column", ["institution_id", "bank_id", "created_by", "submitted_by"])
def test_fdr_foreign_keys_reject_unknown_ids(pg_head, p, column):
    assert _fails(pg_head, lambda s: s.add(FDR(**fdr_kwargs(p, **{column: uuid.uuid4()}))))


def test_history_and_revision_foreign_keys_reject_unknown_ids(pg_head, p):
    fdr_id = add_fdr(pg_head, p)
    assert _fails(pg_head, lambda s: s.add(FDRStatusHistory(
        fdr_id=uuid.uuid4(), to_status="DRAFT", actor_user_id=p.user_id, actor_role="INSTITUTION_USER")))
    assert _fails(pg_head, lambda s: s.add(FDRStatusHistory(
        fdr_id=fdr_id, to_status="DRAFT", actor_user_id=uuid.uuid4(), actor_role="INSTITUTION_USER")))
    assert _fails(pg_head, lambda s: s.add(FDRRevision(
        fdr_id=uuid.uuid4(), revision_no=1, snapshot={"a": "b"}, submitted_by=p.user_id)))
    assert _fails(pg_head, lambda s: s.add(FDRRevision(
        fdr_id=fdr_id, revision_no=1, snapshot={"a": "b"}, submitted_by=uuid.uuid4())))


def test_referenced_institutions_banks_users_and_fdrs_cannot_be_deleted(pg_head, p):
    from app.models import Bank, Institution, User

    fdr_id = add_fdr(pg_head, p)
    add_history(pg_head, p, fdr_id)
    add_revision(pg_head, p, fdr_id)
    for model, key in ((Institution, p.institution_id), (Bank, p.bank_id), (User, p.user_id), (FDR, fdr_id)):
        assert _fails(pg_head, lambda s, m=model, k=key: s.execute(delete(m).where(m.id == k))), model.__name__
    with Session(pg_head) as s:
        assert s.get(FDR, fdr_id) is not None


# ---- exact NUMERIC behaviour -----------------------------------------------------------------
def test_money_round_trips_exactly_as_decimal(pg_head, p):
    fdr_id = add_fdr(pg_head, p, principal_amount=Decimal("1250000.50"), interest_rate_pct=Decimal("7.10"))
    with Session(pg_head) as s:
        fdr = s.get(FDR, fdr_id)
        assert isinstance(fdr.principal_amount, Decimal) and fdr.principal_amount == Decimal("1250000.50")
        assert str(fdr.principal_amount) == "1250000.50" and str(fdr.interest_rate_pct) == "7.10"


def test_largest_representable_amount_is_accepted_and_one_more_overflows(pg_head, p):
    biggest = Decimal("999999999999.99")
    fdr_id = add_fdr(pg_head, p, principal_amount=biggest, fdr_number="BIG", fdr_number_normalized="BIG")
    with Session(pg_head) as s:
        assert s.get(FDR, fdr_id).principal_amount == biggest
    assert _fails(pg_head, lambda s: s.add(FDR(**fdr_kwargs(p, principal_amount=Decimal("1000000000000.00")))), DataError)


def test_floating_point_artifacts_cannot_arise(pg_head, p):
    # 0.1 + 0.2 != 0.3 in binary floating point; NUMERIC keeps decimal arithmetic exact.
    fdr_id = add_fdr(pg_head, p, principal_amount=Decimal("0.10"))
    with Session(pg_head) as s:
        total = s.execute(select(FDR.principal_amount + Decimal("0.20")).where(FDR.id == fdr_id)).scalar()
    assert total == Decimal("0.30")


def test_postgres_rounds_excess_precision_so_the_api_must_reject_it(pg_head, p):
    """Documents why Stage 5c validation must refuse >2 decimal places: the database would
    silently round instead of erroring."""
    fdr_id = add_fdr(pg_head, p, principal_amount=Decimal("10.005"), interest_rate_pct=Decimal("7.125"))
    with Session(pg_head) as s:
        fdr = s.get(FDR, fdr_id)
        assert fdr.principal_amount != Decimal("10.005") and fdr.interest_rate_pct != Decimal("7.125")


def test_a_tiny_amount_that_rounds_to_zero_still_violates_the_positive_check(pg_head, p):
    assert _fails(pg_head, lambda s: s.add(FDR(**fdr_kwargs(p, principal_amount=Decimal("0.004")))))


# ---- catalog checks: the shipped schema is what we think it is -------------------------------
def test_column_types_and_partial_index_in_the_catalog(pg_head, p):
    with pg_head.connect() as conn:
        cols = {(r[0], r[1]): (r[2], r[3], r[4]) for r in conn.execute(text(
            "SELECT table_name, column_name, data_type, numeric_precision, numeric_scale "
            "FROM information_schema.columns WHERE table_schema='public' "
            "AND table_name IN ('fdrs','fdr_status_history','fdr_revisions')"))}
        index_def = conn.execute(text(
            "SELECT indexdef FROM pg_indexes WHERE indexname='uq_fdrs_active_bank_fdr_number'")).scalar()
    assert cols[("fdrs", "principal_amount")] == ("numeric", 14, 2)
    assert cols[("fdrs", "interest_rate_pct")] == ("numeric", 5, 2)
    assert cols[("fdrs", "issue_date")][0] == "date" and cols[("fdrs", "maturity_date")][0] == "date"
    assert cols[("fdrs", "created_at")][0] == "timestamp with time zone"
    assert cols[("fdr_revisions", "snapshot")][0] == "jsonb"
    assert cols[("fdr_status_history", "id")][0] == "bigint"
    assert "UNIQUE" in index_def and "WHERE" in index_def
    for status in ("SUBMITTED", "CORRECTION_REQUESTED", "ACCEPTED"):
        assert status in index_def
    for status in ("DRAFT", "REJECTED", "WITHDRAWN"):
        assert status not in index_def


def test_jsonb_snapshot_keeps_money_as_strings(pg_head, p):
    fdr_id = add_fdr(pg_head, p)
    rev = add_revision(pg_head, p, fdr_id, 1, {"principal_amount": "1250000.50", "interest_rate_pct": "7.10"})
    with Session(pg_head) as s:
        snap = s.get(FDRRevision, rev).snapshot
        assert snap == {"principal_amount": "1250000.50", "interest_rate_pct": "7.10"}
        assert all(isinstance(v, str) for v in snap.values())
        kind = s.execute(text("SELECT jsonb_typeof(snapshot->'principal_amount') FROM fdr_revisions")).scalar()
    assert kind == "string"


# ---- the partial unique index under real concurrency -----------------------------------------
def test_concurrent_promotion_of_duplicate_drafts_lets_exactly_one_win(pg_head, p):
    import threading

    ids = [add_fdr(pg_head, p, institution_id=iid) for iid in (p.institution_id, p.institution2_id)]
    results, barrier = [], threading.Barrier(2)

    def promote(fdr_id):
        with Session(pg_head) as s:
            barrier.wait()
            try:
                s.execute(update(FDR).where(FDR.id == fdr_id).values(status="SUBMITTED"))
                s.commit()
                results.append("ok")
            except IntegrityError:
                s.rollback()
                results.append("duplicate")

    threads = [threading.Thread(target=promote, args=(i,)) for i in ids]
    [t.start() for t in threads]
    [t.join(timeout=60) for t in threads]
    assert sorted(results) == ["duplicate", "ok"]
