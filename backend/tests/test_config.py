import pytest
from pydantic import ValidationError

from app.config import BACKEND_DIR, Settings


def make(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_plain_postgres_url_is_upgraded_to_psycopg_driver():
    s = make(database_url="postgresql://u:p@localhost/db")
    assert s.database_url == "postgresql+psycopg://u:p@localhost/db"


def test_explicit_driver_is_preserved():
    s = make(database_url="postgresql+psycopg://u:p@localhost/db")
    assert s.database_url == "postgresql+psycopg://u:p@localhost/db"


def test_database_url_is_required(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        make()


def test_defaults_are_safe(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("LLM_ENABLED", raising=False)
    s = make(database_url="postgresql://u:p@localhost/db")
    assert s.llm_enabled is False
    assert s.app_env == "development"


def test_relative_storage_dir_resolves_against_backend_dir():
    s = make(database_url="postgresql://u:p@localhost/db", storage_dir="storage")
    assert s.storage_dir == (BACKEND_DIR / "storage").resolve()


def test_invalid_app_env_rejected():
    with pytest.raises(ValidationError):
        make(database_url="postgresql://u:p@localhost/db", app_env="staging")
