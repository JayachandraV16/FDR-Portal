import os
import uuid

# Settings requires DATABASE_URL and SECRET_KEY; tests never connect to the database
# unless they override it. The values below are throwaway test fixtures, not real secrets.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ["APP_ENV"] = "test"
os.environ["SECRET_KEY"] = "test-only-secret-key-not-for-real-use-0123456789"
# Test client talks plain http://testserver, so Secure cookies would never be returned.
# Permitted only because APP_ENV=test.
os.environ["COOKIE_SECURE"] = "false"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from tests.pg_support import alembic_config, make_pg_engine, reset_schema  # noqa: E402


@pytest.fixture(autouse=True)
def _never_touch_configured_database(monkeypatch):
    """Safety net: DATABASE_URL may point at the real development database in this shell, so the
    app's own engine factory is disabled for every test. Tests must inject their own engine."""
    import app.db

    def _refuse():
        raise RuntimeError("Tests must not use the configured DATABASE_URL; inject a test engine")

    monkeypatch.setattr(app.db, "get_engine", _refuse)
    yield
    from app.main import app as fastapi_app

    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def sqlite_engine(tmp_path):
    """Temporary SQLite database migrated to head (stand-in; no append-only triggers)."""
    url = f"sqlite:///{tmp_path / 'unit.sqlite'}"
    command.upgrade(alembic_config(url), "head")
    engine = create_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture
def sqlite_session(sqlite_engine):
    with Session(sqlite_engine) as session:
        yield session


@pytest.fixture(params=["sqlite", "pg"])
def engine(request):
    """Run a test on both backends; the pg variant is skipped unless TEST_DATABASE_URL is set."""
    return request.getfixturevalue("sqlite_engine" if request.param == "sqlite" else "pg_head")


@pytest.fixture
def pg_head():
    """Engine on the dedicated *_test database, schema reset and migrated to head.

    Skipped unless TEST_DATABASE_URL is set; the safety guard in make_pg_engine refuses any
    database that is not a local '*_test' database distinct from DATABASE_URL.
    """
    engine, url, database = make_pg_engine()
    reset_schema(engine, database)
    command.upgrade(alembic_config(url), "head")
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def make_user(sqlite_session):
    """Insert a user row directly (Stage 2 has no user-management API yet)."""
    from app.models import User

    def _make(role="ADMIN", institution_id=None, email=None):
        user = User(
            email=email or f"{uuid.uuid4()}@example.invalid",
            full_name="Test User (Synthetic)",
            password_hash="not-a-real-hash",
            role=role,
            institution_id=institution_id,
        )
        sqlite_session.add(user)
        sqlite_session.flush()
        return user

    return _make
