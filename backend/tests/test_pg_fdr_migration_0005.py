"""Migration 0005 (fdr_document_extractions) on PostgreSQL (skipped unless TEST_DATABASE_URL is set).

Tests are pinned to revision 0005 (not "head"), so later migrations do not change what they check.
Every test starts by resetting the schema of the verified *_test database only
(pg_support.reset_schema re-checks current_database() before dropping anything).
"""

import uuid

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import FDRDocument, FDRDocumentExtraction
from tests.fdr_support import add_fdr, seed_prereqs
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


def _names(engine, sql) -> set[str]:
    with engine.connect() as conn:
        return {r[0] for r in conn.execute(text(sql))}


FUNCS = "SELECT proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'"
TRIGGERS = "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"


def _seed_document(engine):
    p = seed_prereqs(engine)
    fdr_id = add_fdr(engine, p)
    with Session(engine) as s:
        doc = FDRDocument(
            fdr_id=fdr_id, category="OTHER", storage_key=uuid.uuid4().hex, original_filename="a.pdf",
            content_type="application/pdf", size_bytes=1, sha256="b" * 64, uploaded_by=p.user_id,
        )
        s.add(doc)
        s.commit()
        return p, fdr_id, doc.id


def _add_extraction(engine, p, fdr_id, doc_id):
    with Session(engine) as s:
        s.add(FDRDocumentExtraction(document_id=doc_id, fdr_id=fdr_id, status="COMPLETED", extractor="pypdf",
                                    result={"fields": []}, requested_by=p.user_id))
        s.commit()


def test_0004_to_0005_keeps_every_existing_row_and_only_adds_the_extractions_table(pg):
    engine, cfg = pg
    command.upgrade(cfg, "0004")
    p, fdr_id, doc_id = _seed_document(engine)
    before = (_tables(engine), _names(engine, FUNCS), _names(engine, TRIGGERS))
    counts = {t: _scalar(engine, f"SELECT count(*) FROM {t}") for t in ("institutions", "banks", "users", "fdrs", "fdr_documents")}

    command.upgrade(cfg, "0005")

    assert _scalar(engine, "SELECT version_num FROM alembic_version") == "0005"
    assert _tables(engine) == before[0] | {"fdr_document_extractions"}
    assert (_names(engine, FUNCS), _names(engine, TRIGGERS)) == (before[1], before[2])
    assert {t: _scalar(engine, f"SELECT count(*) FROM {t}") for t in counts} == counts
    assert _scalar(engine, "SELECT count(*) FROM fdr_document_extractions") == 0


def test_a_fresh_upgrade_to_0005_stores_json_results(pg):
    engine, cfg = pg
    command.upgrade(cfg, "0005")
    assert _scalar(engine, "SELECT version_num FROM alembic_version") == "0005"
    p, fdr_id, doc_id = _seed_document(engine)
    _add_extraction(engine, p, fdr_id, doc_id)
    assert _scalar(engine, "SELECT data_type FROM information_schema.columns "
                           "WHERE table_name='fdr_document_extractions' AND column_name='result'") == "jsonb"


def test_downgrade_refuses_while_extractions_exist_unless_explicitly_overridden(pg, monkeypatch):
    engine, cfg = pg
    monkeypatch.delenv("FDR_ALLOW_DESTRUCTIVE_DOWNGRADE", raising=False)
    command.upgrade(cfg, "0005")
    p, fdr_id, doc_id = _seed_document(engine)
    _add_extraction(engine, p, fdr_id, doc_id)
    with pytest.raises(RuntimeError, match=r"Refusing to downgrade: table 'fdr_document_extractions'"):
        command.downgrade(cfg, "0004")
    assert _scalar(engine, "SELECT version_num FROM alembic_version") == "0005"
    assert _scalar(engine, "SELECT count(*) FROM fdr_document_extractions") == 1

    monkeypatch.setenv("FDR_ALLOW_DESTRUCTIVE_DOWNGRADE", "1")
    command.downgrade(cfg, "0004")
    assert _scalar(engine, "SELECT version_num FROM alembic_version") == "0004"
    assert "fdr_document_extractions" not in _tables(engine) and "fdr_documents" in _tables(engine)
    assert _scalar(engine, "SELECT count(*) FROM fdr_documents") == 1  # the document itself is untouched


def test_downgrade_of_an_empty_table_needs_no_override_and_reupgrade_works(pg, monkeypatch):
    engine, cfg = pg
    monkeypatch.delenv("FDR_ALLOW_DESTRUCTIVE_DOWNGRADE", raising=False)
    command.upgrade(cfg, "0005")
    command.downgrade(cfg, "0004")
    assert "fdr_document_extractions" not in _tables(engine)
    command.upgrade(cfg, "0005")
    assert "fdr_document_extractions" in _tables(engine)
