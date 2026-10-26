import pytest

from tests.pg_support import UnsafeTestDatabase, assert_safe_test_database

DEV = "postgresql+psycopg://u:p@localhost:5433/fdr_portal"


def test_accepts_separate_local_test_database():
    assert assert_safe_test_database(
        "postgresql+psycopg://u:p@localhost:5433/fdr_portal_test", DEV
    ).database == "fdr_portal_test"


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://u:p@localhost:5433/fdr_portal",  # the dev database
        "postgresql+psycopg://u:p@localhost:5433/postgres",  # not a *_test name
        "postgresql+psycopg://u:p@db.example.com:5432/fdr_portal_test",  # remote host
        "sqlite:///x_test",  # not PostgreSQL
    ],
)
def test_rejects_unsafe_targets(url):
    with pytest.raises(UnsafeTestDatabase):
        assert_safe_test_database(url, DEV)


def test_rejects_test_database_with_same_name_as_dev():
    with pytest.raises(UnsafeTestDatabase):
        assert_safe_test_database(
            "postgresql+psycopg://u:p@localhost:5433/x_test",
            "postgresql+psycopg://u:p@localhost:5433/x_test",
        )


# ---- the configured development database must be visible to the guard (not just the environment)
def test_guard_checks_every_configured_development_url():
    dev_urls = ["postgresql+psycopg://u:p@localhost:5432/test", "postgresql+psycopg://u:p@127.0.0.1:5433/fdr_portal_test"]
    with pytest.raises(UnsafeTestDatabase):  # the second configured URL has the same database name
        assert_safe_test_database("postgresql+psycopg://u:p@127.0.0.1:5433/fdr_portal_test", dev_urls)


def test_configured_urls_include_the_dotenv_value_even_when_the_environment_is_overridden(tmp_path, monkeypatch):
    from tests.pg_support import configured_database_urls

    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nSECRET_KEY=ignored\nDATABASE_URL=postgresql+psycopg://u:p@127.0.0.1:5433/fdr_portal\n",
                        encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", "postgresql://throwaway:x@localhost:5432/test")  # what conftest sets
    urls = configured_database_urls(env_file)
    assert len(urls) == 2 and urls[0].endswith("/test") and urls[1].endswith("/fdr_portal")


def test_dotenv_value_is_used_to_reject_a_test_database_that_matches_it(tmp_path, monkeypatch):
    from tests.pg_support import configured_database_urls

    env_file = tmp_path / ".env"
    env_file.write_text('DATABASE_URL="postgresql+psycopg://u:p@127.0.0.1:5433/shared_test"\n', encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", "postgresql://throwaway:x@localhost:5432/test")
    with pytest.raises(UnsafeTestDatabase):
        assert_safe_test_database("postgresql+psycopg://u:p@127.0.0.1:5433/shared_test", configured_database_urls(env_file))


def test_missing_dotenv_is_fine(tmp_path, monkeypatch):
    from tests.pg_support import configured_database_urls

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert configured_database_urls(tmp_path / "absent.env") == []


def test_the_configured_database_engine_is_unreachable_from_tests():
    """The autouse conftest fixture must leave no path to the application's configured database."""
    import app.db

    with pytest.raises(RuntimeError, match="must not use the configured DATABASE_URL"):
        app.db.get_engine()
    with pytest.raises(RuntimeError, match="must not use the configured DATABASE_URL"):
        app.db.get_session_factory()
