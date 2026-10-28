"""Migration 0004 (fdr_documents) on PostgreSQL (skipped unless TEST_DATABASE_URL is set).

Tests are pinned to revision 0004 (not "head"), so later migrations do not change what they check.
Every test starts by resetting the schema of the verified *_test database only
(pg_support.reset_schema re-checks current_database() before dropping anything).
"""

import uuid

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import FDRDocument
from tests.fdr_support import add_fdr, add_history, add_revision, seed_prereqs
from tests.pg_support import alembic_config, make_pg_engine, reset_schema


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


def _scalar(engine, sql):
    with engine.connect() as conn:
        return conn.execute(text(sql)).scalar()


def _tables(engine) -> set[str]:
    with engine.connect() as conn:
        return {r[0] for r in conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))}


def _functions(engine) -> set[str]:
    with engine.connect() as conn:
        return {r[0] for r in conn.execute(text(
            "SELECT proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'"))}


def _triggers(engine) -> set[str]:
    with engine.connect() as conn:
        return {r[0] for r in conn.execute(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"))}


def _version(engine) -> str:
    return _scalar(engine, "SELECT version_num FROM alembic_version")


def test_0003_to_0004_keeps_every_existing_row_and_only_adds_the_documents_table(pg):
    engine, cfg = pg
    command.upgrade(cfg, "0003")
    p = seed_prereqs(engine)
    fdr_id = add_fdr(engine, p)
    add_history(engine, p, fdr_id)
    add_revision(engine, p, fdr_id)
    before_tables, before_funcs, before_triggers = _tables(engine), _functions(engine), _triggers(engine)
    counts = {t: _scalar(engine, f"SELECT count(*) FROM {t}") for t in ("institutions", "banks", "users", "fdrs", "fdr_status_history", "fdr_revisions")}

    command.upgrade(cfg, "0004")

    assert _version(engine) == "0004"
    assert _tables(engine) == before_tables | {"fdr_documents"}
    assert (_functions(engine), _triggers(engine)) == (before_funcs, before_triggers)  # nothing else was touched
    assert {t: _scalar(engine, f"SELECT count(*) FROM {t}") for t in counts} == counts
    assert _scalar(engine, "SELECT count(*) FROM fdr_documents") == 0


def test_a_fresh_upgrade_to_0004_accepts_a_document(pg):
    engine, cfg = pg
    command.upgrade(cfg, "0004")
    assert _version(engine) == "0004"
    p = seed_prereqs(engine)
    fdr_id = add_fdr(engine, p)
    with Session(engine) as s:
        s.add(FDRDocument(
            fdr_id=fdr_id, category="OTHER", storage_key=uuid.uuid4().hex, original_filename="a.pdf",
            content_type="application/pdf", size_bytes=1, sha256="b" * 64, uploaded_by=p.user_id,
        ))
        s.commit()
    assert _scalar(engine, "SELECT count(*) FROM fdr_documents") == 1


def test_downgrade_refuses_while_documents_exist_unless_explicitly_overridden(pg, monkeypatch):
    engine, cfg = pg
    monkeypatch.delenv("FDR_ALLOW_DESTRUCTIVE_DOWNGRADE", raising=False)
    command.upgrade(cfg, "0004")
    p = seed_prereqs(engine)
    fdr_id = add_fdr(engine, p)
    with Session(engine) as s:
        s.add(FDRDocument(
            fdr_id=fdr_id, category="OTHER", storage_key=uuid.uuid4().hex, original_filename="a.pdf",
            content_type="application/pdf", size_bytes=1, sha256="b" * 64, uploaded_by=p.user_id,
        ))
        s.commit()
    with pytest.raises(RuntimeError, match=r"Refusing to downgrade: table 'fdr_documents'"):
        command.downgrade(cfg, "0003")
    assert _version(engine) == "0004" and _scalar(engine, "SELECT count(*) FROM fdr_documents") == 1

    monkeypatch.setenv("FDR_ALLOW_DESTRUCTIVE_DOWNGRADE", "1")
    command.downgrade(cfg, "0003")
    assert _version(engine) == "0003" and "fdr_documents" not in _tables(engine)
    assert _scalar(engine, "SELECT count(*) FROM fdrs") == 1  # the FDR itself is untouched


def test_downgrade_of_an_empty_table_needs_no_override_and_reupgrade_works(pg, monkeypatch):
    engine, cfg = pg
    monkeypatch.delenv("FDR_ALLOW_DESTRUCTIVE_DOWNGRADE", raising=False)
    command.upgrade(cfg, "0004")
    command.downgrade(cfg, "0003")
    assert _version(engine) == "0003" and "fdr_documents" not in _tables(engine)
    command.upgrade(cfg, "0004")
    assert _version(engine) == "0004" and "fdr_documents" in _tables(engine)
