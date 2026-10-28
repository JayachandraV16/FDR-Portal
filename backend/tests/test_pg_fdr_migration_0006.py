"""Migration 0006 (fdr_extraction_jobs) on PostgreSQL (skipped unless TEST_DATABASE_URL is set).

Every test starts by resetting the schema of the verified *_test database only
(pg_support.reset_schema re-checks current_database() before dropping anything).
"""

import uuid

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import FDRDocument, FDRDocumentExtraction, FDRExtractionJob
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


def _names(engine, sql) -> set[str]:
    with engine.connect() as conn:
        return {r[0] for r in conn.execute(text(sql))}


TABLES = "SELECT tablename FROM pg_tables WHERE schemaname='public'"
FUNCS = "SELECT proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'"
TRIGGERS = "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"
EXTRACTION_COLUMNS = "SELECT column_name FROM information_schema.columns WHERE table_name='fdr_document_extractions'"


def _seed(engine):
    p = seed_prereqs(engine)
    fdr_id = add_fdr(engine, p)
    with Session(engine) as s:
        doc = FDRDocument(fdr_id=fdr_id, category="OTHER", storage_key=uuid.uuid4().hex, original_filename="a.pdf",
                          content_type="application/pdf", size_bytes=1, sha256="b" * 64, uploaded_by=p.user_id)
        s.add(doc)
        s.commit()
        extraction = FDRDocumentExtraction(document_id=doc.id, fdr_id=fdr_id, status="COMPLETED", extractor="pypdf",
                                           result={"fields": []}, requested_by=p.user_id)
        s.add(extraction)
        s.commit()
        return p, fdr_id, doc.id


def _add_job(engine, p, fdr_id, doc_id, status="PENDING"):
    with Session(engine) as s:
        s.add(FDRExtractionJob(document_id=doc_id, fdr_id=fdr_id, requested_by=p.user_id, status=status))
        s.commit()


def test_0005_to_0006_keeps_every_existing_row_and_only_adds_the_jobs_table(pg):
    engine, cfg = pg
    command.upgrade(cfg, "0005")
    _seed(engine)
    before = (_names(engine, TABLES), _names(engine, FUNCS), _names(engine, TRIGGERS))
    counts = {t: _scalar(engine, f"SELECT count(*) FROM {t}")
              for t in ("institutions", "banks", "users", "fdrs", "fdr_documents", "fdr_document_extractions")}
    columns_before = _names(engine, EXTRACTION_COLUMNS)

    command.upgrade(cfg, "0006")

    assert _scalar(engine, "SELECT version_num FROM alembic_version") == "0006"
    assert _names(engine, TABLES) == before[0] | {"fdr_extraction_jobs"}
    assert (_names(engine, FUNCS), _names(engine, TRIGGERS)) == (before[1], before[2])
    assert {t: _scalar(engine, f"SELECT count(*) FROM {t}") for t in counts} == counts
    assert _names(engine, EXTRACTION_COLUMNS) == columns_before  # the table from 0005 is untouched
    assert _scalar(engine, "SELECT count(*) FROM fdr_extraction_jobs") == 0


def test_the_active_job_index_is_partial_and_enforced(pg):
    engine, cfg = pg
    command.upgrade(cfg, "0006")
    assert _scalar(engine, "SELECT version_num FROM alembic_version") == "0006"
    p, fdr_id, doc_id = _seed(engine)
    _add_job(engine, p, fdr_id, doc_id, "RUNNING")
    with pytest.raises(IntegrityError, match="uq_fdr_extraction_jobs_one_active_per_document"):
        _add_job(engine, p, fdr_id, doc_id, "PENDING")
    with engine.begin() as conn:
        conn.execute(text("UPDATE fdr_extraction_jobs SET status='COMPLETED'"))
    _add_job(engine, p, fdr_id, doc_id, "PENDING")  # a finished job no longer blocks a new one
    definition = _scalar(engine, "SELECT indexdef FROM pg_indexes WHERE indexname='uq_fdr_extraction_jobs_one_active_per_document'")
    assert "WHERE" in definition and "UNIQUE" in definition


def test_downgrade_refuses_while_jobs_exist_unless_explicitly_overridden(pg, monkeypatch):
    engine, cfg = pg
    monkeypatch.delenv("FDR_ALLOW_DESTRUCTIVE_DOWNGRADE", raising=False)
    command.upgrade(cfg, "0006")
    p, fdr_id, doc_id = _seed(engine)
    _add_job(engine, p, fdr_id, doc_id)
    with pytest.raises(RuntimeError, match=r"Refusing to downgrade: table 'fdr_extraction_jobs'"):
        command.downgrade(cfg, "0005")
    assert _scalar(engine, "SELECT version_num FROM alembic_version") == "0006"
    assert _scalar(engine, "SELECT count(*) FROM fdr_extraction_jobs") == 1

    monkeypatch.setenv("FDR_ALLOW_DESTRUCTIVE_DOWNGRADE", "1")
    command.downgrade(cfg, "0005")
    assert _scalar(engine, "SELECT version_num FROM alembic_version") == "0005"
    assert "fdr_extraction_jobs" not in _names(engine, TABLES) and "fdr_document_extractions" in _names(engine, TABLES)
    assert _scalar(engine, "SELECT count(*) FROM fdr_document_extractions") == 1  # results are untouched


def test_downgrade_of_an_empty_table_needs_no_override_and_reupgrade_works(pg, monkeypatch):
    engine, cfg = pg
    monkeypatch.delenv("FDR_ALLOW_DESTRUCTIVE_DOWNGRADE", raising=False)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0005")
    assert "fdr_extraction_jobs" not in _names(engine, TABLES)
    command.upgrade(cfg, "head")
    assert "fdr_extraction_jobs" in _names(engine, TABLES)
