"""Database constraints for fdr_document_extractions (SQLite, and PostgreSQL when TEST_DATABASE_URL is set).
Foreign keys are enforced by PostgreSQL only, so those checks skip on the SQLite variant."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FDRDocument, FDRDocumentExtraction, FDRExtractionJob
from tests.fdr_support import add_fdr, rejected_by_database, seed_prereqs, sqlite_decimal_warning

pytestmark = sqlite_decimal_warning


@pytest.fixture
def world(engine):
    p = seed_prereqs(engine)
    fdr_id = add_fdr(engine, p)
    with Session(engine) as s:
        doc = FDRDocument(
            fdr_id=fdr_id, category="OTHER", storage_key=uuid.uuid4().hex, original_filename="a.pdf",
            content_type="application/pdf", size_bytes=1, sha256="a" * 64, uploaded_by=p.user_id,
        )
        s.add(doc)
        s.commit()
        return p, fdr_id, doc.id


def kwargs(world, **overrides):
    p, fdr_id, doc_id = world
    values = dict(document_id=doc_id, fdr_id=fdr_id, status="COMPLETED", error_code=None, extractor="pypdf 6",
                  result={"fields": []}, requested_by=p.user_id)
    values.update(overrides)
    return values


def rejected(engine, world, **overrides) -> bool:
    return rejected_by_database(engine, lambda s: s.add(FDRDocumentExtraction(**kwargs(world, **overrides))))


def test_valid_completed_and_failed_rows_are_accepted(engine, world):
    assert not rejected(engine, world)
    assert not rejected(engine, world, status="FAILED", error_code="OCR_UNAVAILABLE")
    with Session(engine) as s:
        rows = list(s.scalars(select(FDRDocumentExtraction)))
        assert len(rows) == 2 and all(r.requested_at is not None for r in rows)


@pytest.mark.parametrize(
    "overrides",
    [{"status": "RUNNING"}, {"status": "FAILED"}, {"status": "COMPLETED", "error_code": "TIMEOUT"}, {"result": None},
     {"extractor": None}],
)
def test_invalid_rows_are_rejected(engine, world, overrides):
    assert rejected(engine, world, **overrides)


def test_foreign_keys_are_enforced_on_postgresql(engine, world):
    if "sqlite" in engine.url.drivername:
        pytest.skip("SQLite does not enforce foreign keys here")
    assert rejected(engine, world, document_id=uuid.uuid4())
    assert rejected(engine, world, fdr_id=uuid.uuid4())
    assert rejected(engine, world, requested_by=uuid.uuid4())


# ---- extraction jobs (migration 0006) ---------------------------------------------------------------
def job_kwargs(world, **overrides):
    p, fdr_id, doc_id = world
    values = dict(document_id=doc_id, fdr_id=fdr_id, requested_by=p.user_id, status="PENDING")
    values.update(overrides)
    return values


def job_rejected(engine, world, **overrides) -> bool:
    return rejected_by_database(engine, lambda s: s.add(FDRExtractionJob(**job_kwargs(world, **overrides))))


def test_a_pending_job_gets_defaults(engine, world):
    assert not job_rejected(engine, world)
    with Session(engine) as s:
        job = s.scalars(select(FDRExtractionJob)).one()
        assert job.status == "PENDING" and job.attempts == 0 and job.requested_at is not None
        assert job.started_at is None and job.claimed_by is None and job.lease_expires_at is None and job.extraction_id is None


@pytest.mark.parametrize(
    "overrides",
    [{"status": "QUEUED"}, {"status": "FAILED"}, {"status": "COMPLETED", "error_code": "TIMEOUT"}, {"attempts": -1}],
)
def test_invalid_job_rows_are_rejected(engine, world, overrides):
    assert job_rejected(engine, world, **overrides)


def test_at_most_one_job_per_document_can_be_active_but_finished_ones_pile_up(engine, world):
    assert not job_rejected(engine, world, status="PENDING")
    assert job_rejected(engine, world, status="PENDING")
    assert job_rejected(engine, world, status="RUNNING")
    with Session(engine) as s:  # finish the first; now a new active job is allowed again
        s.scalars(select(FDRExtractionJob)).one().status = "COMPLETED"
        s.commit()
    assert not job_rejected(engine, world, status="RUNNING")
    assert not job_rejected(engine, world, status="FAILED", error_code="TIMEOUT")
    assert not job_rejected(engine, world, status="COMPLETED")
    assert not job_rejected(engine, world, status="FAILED", error_code="INTERRUPTED")
    with Session(engine) as s:
        assert len(list(s.scalars(select(FDRExtractionJob)))) == 5


def test_job_foreign_keys_are_enforced_on_postgresql(engine, world):
    if "sqlite" in engine.url.drivername:
        pytest.skip("SQLite does not enforce foreign keys here")
    assert job_rejected(engine, world, document_id=uuid.uuid4())
    assert job_rejected(engine, world, fdr_id=uuid.uuid4())
    assert job_rejected(engine, world, requested_by=uuid.uuid4())
    assert job_rejected(engine, world, extraction_id=uuid.uuid4())
