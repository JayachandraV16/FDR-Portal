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


URL = "postgresql://u:p@localhost/db"
GOOD_KEY = "k" * 32


def test_secret_key_is_required_and_minimum_length(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(ValidationError):
        make(database_url=URL)
    with pytest.raises(ValidationError):
        make(database_url=URL, secret_key="short")


def test_secret_key_is_not_exposed_in_repr():
    s = make(database_url=URL, secret_key=GOOD_KEY)
    assert GOOD_KEY not in repr(s)
    assert GOOD_KEY not in str(s)


def test_cookie_defaults_are_secure_and_strict(monkeypatch):
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    monkeypatch.delenv("COOKIE_SAMESITE", raising=False)
    s = make(database_url=URL, secret_key=GOOD_KEY)
    assert s.cookie_secure is True
    assert s.cookie_samesite == "strict"


def test_insecure_cookie_allowed_only_outside_production():
    make(database_url=URL, secret_key=GOOD_KEY, cookie_secure=False, app_env="development")
    with pytest.raises(ValidationError):
        make(database_url=URL, secret_key=GOOD_KEY, cookie_secure=False, app_env="production")


def test_idle_timeout_cannot_exceed_absolute_timeout():
    with pytest.raises(ValidationError):
        make(
            database_url=URL,
            secret_key=GOOD_KEY,
            session_idle_timeout_minutes=600,
            session_absolute_timeout_minutes=60,
        )


@pytest.mark.parametrize("bad", [["*"], ["localhost:5173"], ["http://localhost:5173/path"], ["not an origin"]])
def test_cors_origins_must_be_explicit_origins(bad):
    with pytest.raises(ValidationError):
        make(database_url=URL, secret_key=GOOD_KEY, cors_origins=bad)


def test_cors_origins_accepts_explicit_origins():
    s = make(database_url=URL, secret_key=GOOD_KEY, cors_origins=["http://localhost:5173", "https://portal.example.invalid"])
    assert len(s.cors_origins) == 2


def test_invalid_app_env_rejected():
    with pytest.raises(ValidationError):
        make(database_url="postgresql://u:p@localhost/db", app_env="staging")
