"""Schema/migration/seed checks run against a temporary SQLite file.

SQLite is only a stand-in so these run without a server; they do not prove the
migration works on PostgreSQL (see README for the PostgreSQL smoke test).
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models import Bank, Base, Institution
from app.seed import DEMO_BANKS, DEMO_INSTITUTIONS, seed

BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture
def migrated_url(tmp_path):
    url = f"sqlite:///{tmp_path / 'test.sqlite'}"
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    return url


def test_migration_matches_models(migrated_url):
    engine = create_engine(migrated_url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_seed_is_idempotent(migrated_url):
    engine = create_engine(migrated_url)
    with Session(engine) as s:
        first = seed(s)
        second = seed(s)
        assert first == {"banks": len(DEMO_BANKS), "institutions": len(DEMO_INSTITUTIONS)}
        assert second == {"banks": 0, "institutions": 0}
        assert s.scalar(select(func.count()).select_from(Institution)) == len(DEMO_INSTITUTIONS)
        assert s.scalar(select(func.count()).select_from(Bank)) == len(DEMO_BANKS)


def test_seed_leaves_existing_rows_untouched(migrated_url):
    engine = create_engine(migrated_url)
    with Session(engine) as s:
        s.add(Bank(code="SYN-BANK-01", name="Sample National Bank (Synthetic)"))
        s.add(Bank(code="SBI", name="Locally Edited Name"))
        s.commit()
        seed(s)
        assert s.scalar(select(Bank.name).where(Bank.code == "SYN-BANK-01")) == "Sample National Bank (Synthetic)"
        assert s.scalar(select(Bank.name).where(Bank.code == "SBI")) == "Locally Edited Name"
        assert s.scalar(select(func.count()).select_from(Bank)) == len(DEMO_BANKS) + 1


def test_demo_codes_are_unique_and_fit_the_schema():
    codes = [c for c, _ in DEMO_BANKS] + [c for c, *_ in DEMO_INSTITUTIONS]
    assert len(codes) == len(set(codes))
    assert all(len(c) <= 32 for c in codes)
