"""Helpers for PostgreSQL-only tests.

These tests run only when TEST_DATABASE_URL is set, and they DROP AND RECREATE the public
schema of that database. `assert_safe_test_database` must pass before anything destructive
happens, so the development database can never be targeted.
"""

import os
from collections.abc import Iterable
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url

BACKEND_DIR = Path(__file__).resolve().parent.parent
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class UnsafeTestDatabase(RuntimeError):
    pass


def _normalise(url: URL) -> tuple[str, int, str]:
    return (url.host or "localhost", url.port or 5432, url.database or "")


def configured_database_urls(env_file: Path | None = None) -> list[str]:
    """Every DATABASE_URL the application could be configured with: the process environment and
    backend/.env. conftest.py pre-sets DATABASE_URL to a throwaway value, so the environment alone
    would hide the real development database from the safety check. Values are never printed."""
    urls = []
    if os.environ.get("DATABASE_URL"):
        urls.append(os.environ["DATABASE_URL"])
    path = env_file if env_file is not None else BACKEND_DIR / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                value = line.split("=", 1)[1].strip().strip("\"'")
                if value:
                    urls.append(value)
    return urls


def assert_safe_test_database(test_url: str, dev_urls: str | Iterable[str] | None) -> URL:
    """Raise unless `test_url` is clearly a disposable local test database that is not any
    configured development database."""
    test = make_url(test_url)
    if not test.drivername.startswith("postgresql"):
        raise UnsafeTestDatabase("TEST_DATABASE_URL must be a PostgreSQL URL")
    host, _port, database = _normalise(test)
    if host not in LOCAL_HOSTS:
        raise UnsafeTestDatabase(f"Test database host must be local, got {host!r}")
    if not database.endswith("_test"):
        raise UnsafeTestDatabase(f"Test database name must end with '_test', got {database!r}")
    if isinstance(dev_urls, str):
        dev_urls = [dev_urls]
    for dev_url in dev_urls or []:
        dev = make_url(dev_url)
        if dev.database == test.database and (dev.host or "localhost") == host:
            raise UnsafeTestDatabase("TEST_DATABASE_URL must not equal DATABASE_URL")
        if dev.database == test.database:
            raise UnsafeTestDatabase("TEST_DATABASE_URL has the same database name as DATABASE_URL")
    return test


def alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    # configparser treats '%' specially, so escape it.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def reset_schema(engine: Engine, expected_database: str) -> None:
    """Drop and recreate `public`, after re-verifying which database we are connected to."""
    with engine.begin() as conn:
        actual = conn.execute(text("SELECT current_database()")).scalar()
        if actual != expected_database or not actual.endswith("_test"):
            raise UnsafeTestDatabase(f"Connected to {actual!r}; refusing to reset schema")
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


def make_pg_engine() -> tuple[Engine, str, str]:
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("TEST_DATABASE_URL not set; PostgreSQL-only tests skipped")
    test = assert_safe_test_database(raw, configured_database_urls())
    url = test.render_as_string(hide_password=False)
    return create_engine(url, connect_args={"connect_timeout": 10}), url, test.database
