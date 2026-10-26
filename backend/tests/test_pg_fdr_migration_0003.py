"""Migration 0003 on PostgreSQL (skipped unless TEST_DATABASE_URL is set).

Every test starts by resetting the schema of the verified *_test database only
(pg_support.reset_schema re-checks current_database() before dropping anything).
"""

import uuid

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.fdr_support import add_fdr, add_history, add_revision, seed_prereqs
from tests.pg_support import alembic_config, make_pg_engine, reset_schema

NEW_TABLES = {"fdrs", "fdr_status_history", "fdr_revisions"}


@pytest.fixture
def pg():
    engine, url, database = make_pg_engine()
    reset_schema(engine, database)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT current_database()")).scalar() == database
        assert database.endswith("_test")
    try:
        yield engine, alembic_config(url)
    finally:
        engine.dispose()


def _tables(engine) -> set[str]:
    with engine.connect() as conn:
        return {r[0] for r in conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))}


def _functions(engine) -> set[str]:
    with engine.connect() as conn:
        return {r[0] for r in conn.execute(text(
            "SELECT proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'"))}


def _triggers(engine) -> set[str]:
    with engine.connect() as conn:
        return {r[0] for r in conn.execute(text(
            "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"))}


def _version(engine) -> str:
    with engine.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def _count(engine, table) -> int:
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()


# ---- upgrade ---------------------------------------------------------------------------------
def test_upgrade_0002_to_0003_preserves_all_existing_data(pg):
    engine, cfg = pg
    command.upgrade(cfg, "0002")
    p = seed_prereqs(engine)  # institutions, banks and a user exist before 0003
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO audit_logs (occurred_at, action, outcome, hash) VALUES (now(), 'pre.existing', 'success', :h)"),
            {"h": "a" * 64})
        conn.execute(text(
            "INSERT INTO auth_sessions (id, user_id, token_hash, expires_at) VALUES (:i, :u, :t, now() + interval '1 day')"),
            {"i": uuid.uuid4(), "u": p.user_id, "t": "b" * 64})
    before = {t: _count(engine, t) for t in ("institutions", "banks", "users", "audit_logs", "auth_sessions")}
    assert before == {"institutions": 2, "banks": 2, "users": 1, "audit_logs": 1, "auth_sessions": 1}
    assert NEW_TABLES.isdisjoint(_tables(engine)) and _version(engine) == "0002"

    command.upgrade(cfg, "0003")

    assert _version(engine) == "0003"
    assert NEW_TABLES <= _tables(engine)
    assert {t: _count(engine, t) for t in before} == before
    for t in NEW_TABLES:
        assert _count(engine, t) == 0
    with engine.connect() as conn:  # the pre-existing row is untouched
        assert conn.execute(text("SELECT action FROM audit_logs")).scalar() == "pre.existing"


def test_upgrade_from_scratch_to_head_reaches_0003(pg):
    engine, cfg = pg
    command.upgrade(cfg, "head")
    assert _version(engine) == "0003" and NEW_TABLES <= _tables(engine)


def test_0003_does_not_touch_the_existing_audit_trigger_or_function(pg):
    engine, cfg = pg
    command.upgrade(cfg, "0002")
    before = (_functions(engine), _triggers(engine))
    command.upgrade(cfg, "0003")
    funcs, trigs = _functions(engine), _triggers(engine)
    assert before[0] <= funcs and before[1] <= trigs  # everything from 0002 is still there
    assert funcs - before[0] == {"append_only_block_modification"}
    assert trigs - before[1] == {
        "fdr_status_history_no_update_delete", "fdr_status_history_no_truncate",
        "fdr_revisions_no_update_delete", "fdr_revisions_no_truncate",
    }


# ---- append-only triggers --------------------------------------------------------------------
@pytest.fixture
def populated(pg):
    engine, cfg = pg
    command.upgrade(cfg, "head")
    p = seed_prereqs(engine)
    fdr_id = add_fdr(engine, p)
    history_id = add_history(engine, p, fdr_id)
    revision_id = add_revision(engine, p, fdr_id, 1, {"principal_amount": "100000.00"})
    return engine, cfg, p, fdr_id, history_id, revision_id


BLOCKED_STATEMENTS = [
    ("fdr_status_history", "UPDATE fdr_status_history SET comment = 'tampered'"),
    ("fdr_status_history", "UPDATE fdr_status_history SET to_status = 'ACCEPTED'"),
    ("fdr_status_history", "DELETE FROM fdr_status_history"),
    ("fdr_status_history", "TRUNCATE fdr_status_history"),
    ("fdr_revisions", "UPDATE fdr_revisions SET revision_no = 99"),
    ("fdr_revisions", "UPDATE fdr_revisions SET snapshot = '{}'::jsonb"),
    ("fdr_revisions", "DELETE FROM fdr_revisions"),
    ("fdr_revisions", "TRUNCATE fdr_revisions"),
]


@pytest.mark.parametrize("table,statement", BLOCKED_STATEMENTS)
def test_history_and_revisions_reject_update_delete_and_truncate(populated, table, statement):
    engine, *_ = populated
    with pytest.raises(DBAPIError, match=f"{table} is append-only"):
        with engine.begin() as conn:
            conn.execute(text(statement))
    assert _count(engine, table) == 1  # nothing changed


def test_tampering_attempts_leave_the_content_intact(populated):
    engine, _cfg, _p, _fdr_id, history_id, revision_id = populated
    for stmt in ("UPDATE fdr_status_history SET comment = 'x'", "DELETE FROM fdr_revisions"):
        with pytest.raises(DBAPIError):
            with engine.begin() as conn:
                conn.execute(text(stmt))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT comment FROM fdr_status_history WHERE id=:i"), {"i": history_id}).scalar() is None
        assert conn.execute(text("SELECT snapshot->>'principal_amount' FROM fdr_revisions WHERE id=:i"),
                            {"i": revision_id}).scalar() == "100000.00"


def test_appending_is_still_allowed_and_the_fdr_row_itself_stays_editable(populated):
    engine, _cfg, p, fdr_id, *_ = populated
    add_history(engine, p, fdr_id, from_status="DRAFT", to_status="SUBMITTED")
    add_revision(engine, p, fdr_id, 2, {"principal_amount": "100000.00"})
    with engine.begin() as conn:  # fdrs is a normal mutable table (status/row_version change over time)
        conn.execute(text("UPDATE fdrs SET status='SUBMITTED', row_version=row_version+1 WHERE id=:i"), {"i": fdr_id})
    assert _count(engine, "fdr_status_history") == 2 and _count(engine, "fdr_revisions") == 2


def test_the_audit_log_trigger_from_0002_still_works_after_0003(populated):
    engine, *_ = populated
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO audit_logs (occurred_at, action, outcome, hash) VALUES (now(),'x','success',:h)"),
                     {"h": "c" * 64})
    with pytest.raises(DBAPIError, match="audit_logs is append-only"):
        with engine.begin() as conn:
            conn.execute(text("UPDATE audit_logs SET action='y'"))


# ---- downgrade guard, downgrade, re-upgrade --------------------------------------------------
def test_downgrade_refuses_when_fdrs_contain_data(populated, monkeypatch):
    engine, cfg, p, *_ = populated
    monkeypatch.delenv("FDR_ALLOW_DESTRUCTIVE_DOWNGRADE", raising=False)
    # Rebuild a scenario where only fdrs has data, to see which table the guard names first.
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))  # test DB only: transaction-scoped trigger bypass to build a state
        conn.execute(text("DELETE FROM fdr_revisions"))
        conn.execute(text("DELETE FROM fdr_status_history"))
    assert _count(engine, "fdrs") == 1
    with pytest.raises(RuntimeError, match=r"Refusing to downgrade: table 'fdrs'"):
        command.downgrade(cfg, "0002")
    assert _version(engine) == "0003" and _count(engine, "fdrs") == 1 and NEW_TABLES <= _tables(engine)


def test_downgrade_guard_names_history_then_revisions_tables(populated, monkeypatch):
    engine, cfg, *_ = populated
    monkeypatch.delenv("FDR_ALLOW_DESTRUCTIVE_DOWNGRADE", raising=False)
    with pytest.raises(RuntimeError, match=r"table 'fdr_revisions'"):  # checked first when everything has data
        command.downgrade(cfg, "0002")
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        conn.execute(text("DELETE FROM fdr_revisions"))
    with pytest.raises(RuntimeError, match=r"table 'fdr_status_history'"):
        command.downgrade(cfg, "0002")
    assert _version(engine) == "0003"
    assert {t: _count(engine, t) for t in NEW_TABLES} == {"fdrs": 1, "fdr_status_history": 1, "fdr_revisions": 0}


def test_downgrade_with_explicit_override_removes_only_the_0003_objects(populated, monkeypatch):
    engine, cfg, *_ = populated
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO audit_logs (occurred_at, action, outcome, hash) VALUES (now(),'keep.me','success',:h)"),
                     {"h": "d" * 64})
    tables_at_0003, funcs_at_0003, trigs_at_0003 = _tables(engine), _functions(engine), _triggers(engine)
    kept = {t: _count(engine, t) for t in ("institutions", "banks", "users", "audit_logs")}

    monkeypatch.setenv("FDR_ALLOW_DESTRUCTIVE_DOWNGRADE", "1")
    command.downgrade(cfg, "0002")

    assert _version(engine) == "0002"
    assert _tables(engine) == tables_at_0003 - NEW_TABLES
    assert _functions(engine) == funcs_at_0003 - {"append_only_block_modification"}
    assert _triggers(engine) == trigs_at_0003 - {
        "fdr_status_history_no_update_delete", "fdr_status_history_no_truncate",
        "fdr_revisions_no_update_delete", "fdr_revisions_no_truncate",
    }
    assert {t: _count(engine, t) for t in kept} == kept  # users/institutions/banks/audit rows all survived
    with pytest.raises(DBAPIError, match="audit_logs is append-only"):  # 0002's trigger is intact
        with engine.begin() as conn:
            conn.execute(text("UPDATE audit_logs SET action='z'"))


def test_downgrade_of_empty_tables_needs_no_override_and_reupgrade_works(pg, monkeypatch):
    engine, cfg = pg
    monkeypatch.delenv("FDR_ALLOW_DESTRUCTIVE_DOWNGRADE", raising=False)
    command.upgrade(cfg, "head")
    p = seed_prereqs(engine)
    command.downgrade(cfg, "0002")
    assert NEW_TABLES.isdisjoint(_tables(engine)) and _version(engine) == "0002"
    assert _count(engine, "institutions") == 2 and _count(engine, "users") == 1  # pre-existing data kept
    command.upgrade(cfg, "head")  # re-upgrade
    assert NEW_TABLES <= _tables(engine) and _version(engine) == "0003"
    add_fdr(engine, p)  # and the recreated schema is functional
    add_history(engine, p, add_fdr(engine, p, fdr_number="Z", fdr_number_normalized="Z"))
    assert _count(engine, "fdrs") == 2
