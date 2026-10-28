"""Database constraints for fdr_documents.

Runs on SQLite (structural stand-in) and on the dedicated PostgreSQL test database when
TEST_DATABASE_URL is set. Foreign keys are only enforced by PostgreSQL, so those checks are
PostgreSQL-only (skipped on the SQLite variant).
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FDRDocument
from tests.fdr_support import add_fdr, rejected_by_database, seed_prereqs, sqlite_decimal_warning

pytestmark = sqlite_decimal_warning


@pytest.fixture
def p(engine):
    return seed_prereqs(engine)


def doc_kwargs(p, fdr_id, **overrides):
    values = dict(
        fdr_id=fdr_id, category="OTHER", storage_key=uuid.uuid4().hex, original_filename="advice.pdf",
        content_type="application/pdf", size_bytes=10, sha256="a" * 64, uploaded_by=p.user_id,
    )
    values.update(overrides)
    return values


def rejected(engine, p, fdr_id, **overrides) -> bool:
    return rejected_by_database(engine, lambda s: s.add(FDRDocument(**doc_kwargs(p, fdr_id, **overrides))))


def test_a_valid_row_is_accepted_with_active_status_and_an_upload_time(engine, p):
    fdr_id = add_fdr(engine, p)
    with Session(engine) as s:
        s.add(FDRDocument(**doc_kwargs(p, fdr_id)))
        s.commit()
        doc = s.scalars(select(FDRDocument)).one()
        assert doc.status == "ACTIVE" and doc.uploaded_at is not None and doc.deleted_at is None and doc.deleted_by is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"category": "PASSPORT"},
        {"status": "PURGED"},
        {"content_type": "text/html"},
        {"content_type": "image/svg+xml"},
        {"content_type": "application/zip"},
        {"size_bytes": 0},
        {"size_bytes": -1},
        {"sha256": "a" * 63},
        {"sha256": "a" * 65},
        {"storage_key": "short"},
        {"storage_key": "k" * 33},
        {"original_filename": ""},
        {"status": "DELETED"},  # deleted needs a deleted_at
        {"deleted_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},  # a deleted_at needs the DELETED status
    ],
)
def test_invalid_rows_are_rejected_by_the_database(engine, p, overrides):
    fdr_id = add_fdr(engine, p)
    assert rejected(engine, p, fdr_id, **overrides)


def test_a_deleted_row_with_a_matching_timestamp_is_accepted(engine, p):
    fdr_id = add_fdr(engine, p)
    assert not rejected(engine, p, fdr_id, status="DELETED", deleted_at=datetime(2026, 1, 1, tzinfo=timezone.utc), deleted_by=p.user_id)


def test_two_documents_cannot_share_a_storage_key(engine, p):
    fdr_id = add_fdr(engine, p)
    key = uuid.uuid4().hex
    assert not rejected(engine, p, fdr_id, storage_key=key)
    assert rejected(engine, p, fdr_id, storage_key=key)


def test_the_same_name_and_content_may_be_stored_twice_under_different_keys(engine, p):
    fdr_id = add_fdr(engine, p)
    assert not rejected(engine, p, fdr_id)
    assert not rejected(engine, p, fdr_id)


def test_foreign_keys_are_enforced_on_postgresql(request, engine, p):
    if "sqlite" in engine.url.drivername:
        pytest.skip("SQLite does not enforce foreign keys here")
    fdr_id = add_fdr(engine, p)
    assert rejected(engine, p, uuid.uuid4())  # no such FDR
    assert rejected(engine, p, fdr_id, uploaded_by=uuid.uuid4())  # no such user
    assert rejected(engine, p, fdr_id, status="DELETED", deleted_at=datetime(2026, 1, 1, tzinfo=timezone.utc), deleted_by=uuid.uuid4())
