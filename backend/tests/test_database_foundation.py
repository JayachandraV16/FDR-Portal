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
from app.seed import SYNTHETIC_BANKS, SYNTHETIC_INSTITUTIONS, seed

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


def test_seed_is_idempotent_and_synthetic(migrated_url):
    engine = create_engine(migrated_url)
    with Session(engine) as s:
        first = seed(s)
        second = seed(s)
        assert first == {"banks": len(SYNTHETIC_BANKS), "institutions": len(SYNTHETIC_INSTITUTIONS)}
        assert second == {"banks": 0, "institutions": 0}
        assert s.scalar(select(func.count()).select_from(Institution)) == len(SYNTHETIC_INSTITUTIONS)
        names = list(s.scalars(select(Bank.name))) + list(s.scalars(select(Institution.name)))
        assert all("(Synthetic)" in n for n in names)
