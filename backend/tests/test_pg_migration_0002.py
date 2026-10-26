"""PostgreSQL-only checks for migration 0002 (skipped unless TEST_DATABASE_URL is set)."""

import uuid

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.pg_support import alembic_config, make_pg_engine, reset_schema


@pytest.fixture
def pg():
    engine, url, database = make_pg_engine()
    reset_schema(engine, database)
    try:
        yield engine, alembic_config(url)
    finally:
        engine.dispose()


def _insert_institution(conn, code="T-1"):
    iid = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO institutions (id, code, name, state, institution_type, status) "
            "VALUES (:id, :c, 'Test Institution (Synthetic)', 'X', 'Engineering', 'active')"
        ),
        {"id": iid, "c": code},
    )
    return iid


def _insert_user(conn, role="ADMIN", institution_id=None, email=None):
    uid = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO users (id, email, full_name, password_hash, role, institution_id) "
            "VALUES (:id, :e, 'Test User', 'not-a-real-hash', :r, :i)"
        ),
        {"id": uid, "e": email or f"{uid}@example.invalid", "r": role, "i": institution_id},
    )
    return uid


def _insert_audit(conn, n=1):
    conn.execute(
        text(
            "INSERT INTO audit_logs (occurred_at, action, outcome, hash) "
            "VALUES (now(), 'test.event', 'success', :h)"
        ),
        {"h": uuid.uuid4().hex + uuid.uuid4().hex},
    )


def test_upgrade_from_0001_preserves_existing_data(pg):
    engine, cfg = pg
    command.upgrade(cfg, "0001")
    with engine.begin() as conn:
        _insert_institution(conn)
        conn.execute(text("INSERT INTO banks (id, code, name) VALUES (:i, 'B-1', 'Bank (Synthetic)')"), {"i": uuid.uuid4()})
    command.upgrade(cfg, "0002")  # pinned: this test is about migration 0002, not whatever head is
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM institutions")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM banks")).scalar() == 1
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0002"
        tables = {r[0] for r in conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))}
    assert {"users", "auth_sessions", "audit_logs", "banks", "institutions"} <= tables


def test_audit_log_rejects_update_delete_and_truncate(pg):
    engine, cfg = pg
    command.upgrade(cfg, "head")
    with engine.begin() as conn:
        _insert_audit(conn)
    for stmt in ("UPDATE audit_logs SET action = 'tampered'", "DELETE FROM audit_logs", "TRUNCATE audit_logs"):
        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as conn:
                conn.execute(text(stmt))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT action FROM audit_logs")).scalar() == "test.event"


def test_audit_log_allows_inserts_with_increasing_seq(pg):
    engine, cfg = pg
    command.upgrade(cfg, "head")
    with engine.begin() as conn:
        _insert_audit(conn)
        _insert_audit(conn)
        seqs = [r[0] for r in conn.execute(text("SELECT seq FROM audit_logs ORDER BY seq"))]
    assert len(seqs) == 2 and seqs[0] < seqs[1]


def test_audit_outcome_check_constraint(pg):
    engine, cfg = pg
    command.upgrade(cfg, "head")
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO audit_logs (occurred_at, action, outcome, hash) VALUES (now(), 'x', 'bogus', 'h1')")
            )


def test_user_role_and_institution_constraints(pg):
    engine, cfg = pg
    command.upgrade(cfg, "head")
    with engine.begin() as conn:
        iid = _insert_institution(conn)
        _insert_user(conn, "INSTITUTION_USER", iid)  # valid
        _insert_user(conn, "ADMIN", None)  # valid
    bad = [
        ("INSTITUTION_USER", None),  # institution user needs an institution
        ("ADMIN", iid),  # non-institution role must not have one
        ("AUDITOR", iid),
        ("SUPERUSER", None),  # unknown role
    ]
    for role, inst in bad:
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                _insert_user(conn, role, inst)


def test_user_email_unique_and_institution_fk(pg):
    engine, cfg = pg
    command.upgrade(cfg, "head")
    with engine.begin() as conn:
        _insert_user(conn, "ADMIN", email="dup@example.invalid")
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            _insert_user(conn, "AUDITOR", email="dup@example.invalid")
    with pytest.raises(IntegrityError):  # institution that does not exist
        with engine.begin() as conn:
            _insert_user(conn, "INSTITUTION_USER", uuid.uuid4())


def test_downgrade_on_empty_tables_restores_0001_and_removes_triggers(pg):
    engine, cfg = pg
    command.upgrade(cfg, "0001")
    with engine.begin() as conn:
        _insert_institution(conn)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0001")
    with engine.connect() as conn:
        tables = {r[0] for r in conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))}
        funcs = conn.execute(text("SELECT count(*) FROM pg_proc WHERE proname='audit_logs_block_modification'")).scalar()
        assert conn.execute(text("SELECT count(*) FROM institutions")).scalar() == 1
    assert {"users", "auth_sessions", "audit_logs"}.isdisjoint(tables)
    assert funcs == 0
    command.upgrade(cfg, "head")  # re-upgrade works


def test_downgrade_refuses_when_data_would_be_lost(pg, monkeypatch):
    engine, cfg = pg
    monkeypatch.delenv("FDR_ALLOW_DESTRUCTIVE_DOWNGRADE", raising=False)
    command.upgrade(cfg, "0002")  # pinned: asserts 0002's own guard and resulting version below
    with engine.begin() as conn:
        _insert_user(conn, "ADMIN")
        _insert_audit(conn)
    with pytest.raises(RuntimeError, match="Refusing to downgrade"):
        command.downgrade(cfg, "0001")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM users")).scalar() == 1
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0002"


def test_downgrade_with_explicit_override_succeeds(pg, monkeypatch):
    engine, cfg = pg
    command.upgrade(cfg, "head")
    with engine.begin() as conn:
        _insert_user(conn, "ADMIN")
        _insert_audit(conn)
    monkeypatch.setenv("FDR_ALLOW_DESTRUCTIVE_DOWNGRADE", "1")
    command.downgrade(cfg, "0001")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0001"
