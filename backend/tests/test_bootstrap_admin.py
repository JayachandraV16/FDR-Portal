"""Development-only first-administrator bootstrap (app.bootstrap_admin).

Everything runs on temporary SQLite files, or on the dedicated *_test PostgreSQL database for the
concurrency/identity tests (skipped unless TEST_DATABASE_URL is set). Nothing here can reach the
configured development database: conftest disables app.db.get_engine.
"""

import io
import json
import threading

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import verify_chain
from app.bootstrap_admin import (
    AdminAlreadyExists,
    BootstrapRefused,
    check_target_url,
    create_first_admin,
    run,
    verify_connected_database,
)
from app.config import Settings
from app.models import AuditLog, User
from app.security.passwords import verify_password
from tests.api_support import API, NEW_PW, build_client, create_user, login, make_settings

DEV_URL = "postgresql://u:p@127.0.0.1:5433/fdr_portal"  # parsed only, never connected to
EMAIL = "first.admin@example.invalid"
ARGS = ["--email", EMAIL, "--confirm-database", "fdr_portal"]


def dev_settings(**overrides) -> Settings:
    values = dict(app_env="development", database_url=DEV_URL)
    values.update(overrides)
    return make_settings(**values)


def run_bootstrap(engine, argv=None, settings=None):
    out, err = io.StringIO(), io.StringIO()
    code = run(argv or ARGS, settings=settings or dev_settings(), engine=engine,
               out=out, err=err, allow_non_postgres=True)
    return code, out.getvalue(), err.getvalue()


def temp_password(output: str) -> str:
    (line,) = [l for l in output.splitlines() if l.startswith("Temporary password")]
    return line.split(": ", 1)[1]


def all_users(engine):
    with Session(engine) as s:
        return list(s.scalars(select(User)))


def dump_database(engine) -> str:
    """Every column of every users/audit_logs row as text, to search for leaked secrets."""
    with Session(engine) as s:
        users = [[str(getattr(u, c.name)) for c in User.__table__.columns if c.name != "password_hash"]
                 for u in s.scalars(select(User))]
        events = [[str(getattr(e, c.name)) for c in AuditLog.__table__.columns] for e in s.scalars(select(AuditLog))]
    return json.dumps([users, events])


# --- success -----------------------------------------------------------------------------------

def test_creates_one_active_admin_who_must_change_the_password(sqlite_engine):
    code, out, err = run_bootstrap(sqlite_engine)
    assert code == 0 and err == ""
    (user,) = all_users(sqlite_engine)
    assert (user.email, user.role, user.is_active, user.institution_id) == (EMAIL, "ADMIN", True, None)
    assert user.must_change_password is True
    assert user.password_hash.startswith("$argon2id$")
    assert verify_password(user.password_hash, temp_password(out)).valid


def test_email_is_normalised_like_every_other_account(sqlite_engine):
    code, _, _ = run_bootstrap(sqlite_engine, ["--email", "  First.Admin@Example.INVALID ", "--confirm-database", "fdr_portal"])
    assert code == 0 and all_users(sqlite_engine)[0].email == EMAIL


# --- development-only restriction --------------------------------------------------------------

@pytest.mark.parametrize("env,extra", [("test", {}), ("production", {"cookie_secure": True})])
def test_refuses_outside_development(sqlite_engine, env, extra):
    code, out, err = run_bootstrap(sqlite_engine, settings=dev_settings(app_env=env, **extra))
    assert code == 2 and "development" in err and out == ""
    assert all_users(sqlite_engine) == []


def test_refuses_when_app_env_is_not_set_explicitly(sqlite_engine, monkeypatch):
    monkeypatch.delenv("APP_ENV")  # conftest sets it; unset means the config default (development)
    ambiguous = Settings(_env_file=None, database_url=DEV_URL, secret_key="x" * 40, cookie_secure=False)
    assert ambiguous.app_env == "development"  # the default would silently pass if not checked
    code, _, err = run_bootstrap(sqlite_engine, settings=ambiguous)
    assert code == 2 and "not set explicitly" in err
    assert all_users(sqlite_engine) == []


# --- wrong-database protection -----------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "postgresql://u:p@127.0.0.1:5433/fdr_portal_test",   # the test database
    "postgresql://u:p@127.0.0.1:5433/other_project",     # another database on the same server
    "postgresql://u:p@127.0.0.1:5432/fdr_portal",        # the other project's server port
    "postgresql://u:p@127.0.0.1/fdr_portal",              # port unspecified
    "postgresql://u:p@db.example.com:5433/fdr_portal",   # not local
    "sqlite:///dev.sqlite",
])
def test_refuses_any_other_database_target(sqlite_engine, url):
    with pytest.raises(BootstrapRefused):
        check_target_url(url)
    code, _, err = run_bootstrap(sqlite_engine, settings=dev_settings(database_url=url))
    assert code == 2 and err.startswith("Refused:")
    assert all_users(sqlite_engine) == []


def test_requires_the_database_name_to_be_confirmed(sqlite_engine):
    code, _, err = run_bootstrap(sqlite_engine, ["--email", EMAIL, "--confirm-database", "fdr_portal_test"])
    assert code == 2 and "--confirm-database" in err
    assert all_users(sqlite_engine) == []


def test_refuses_a_non_postgres_connection_unless_a_test_allows_it(sqlite_engine):
    with pytest.raises(BootstrapRefused):
        verify_connected_database(sqlite_engine)


def test_live_connection_to_the_test_database_is_refused(pg_head):
    with pytest.raises(BootstrapRefused, match="not 'fdr_portal'"):
        verify_connected_database(pg_head)


def test_refuses_unmigrated_schema(tmp_path):
    from sqlalchemy import create_engine

    empty = create_engine(f"sqlite:///{tmp_path / 'empty.sqlite'}")
    code, _, err = run_bootstrap(empty)
    assert code == 2 and "run the Alembic migrations" in err


# --- existing administrators -------------------------------------------------------------------

@pytest.mark.parametrize("is_active", [True, False])
def test_refuses_when_any_administrator_exists(sqlite_engine, is_active):
    create_user(sqlite_engine, email="existing@example.invalid", role="ADMIN", is_active=is_active)
    code, out, err = run_bootstrap(sqlite_engine)
    assert code == 2 and "already exists" in err and out == ""
    assert [u.email for u in all_users(sqlite_engine)] == ["existing@example.invalid"]


def test_running_twice_creates_only_one_administrator(sqlite_engine):
    assert run_bootstrap(sqlite_engine)[0] == 0
    code, _, _ = run_bootstrap(sqlite_engine, ["--email", "second@example.invalid", "--confirm-database", "fdr_portal"])
    assert code == 2 and len(all_users(sqlite_engine)) == 1


def test_non_admin_users_do_not_block_but_their_email_cannot_be_reused(sqlite_engine):
    create_user(sqlite_engine, email=EMAIL, role="AUDITOR")
    code, _, err = run_bootstrap(sqlite_engine)
    assert code == 2 and "already exists" in err
    create_user(sqlite_engine, email="auditor@example.invalid", role="AUDITOR")
    assert run_bootstrap(sqlite_engine, ["--email", "ok@example.invalid", "--confirm-database", "fdr_portal"])[0] == 0


@pytest.mark.parametrize("email,name", [("not-an-email", "Admin"), (EMAIL, "   "), (EMAIL, "x" * 201)])
def test_rejects_invalid_input(sqlite_engine, email, name):
    argv = ["--email", email, "--full-name", name, "--confirm-database", "fdr_portal"]
    assert run_bootstrap(sqlite_engine, argv)[0] == 2
    assert all_users(sqlite_engine) == []


# --- temporary password handling ---------------------------------------------------------------

def test_password_is_strong_shown_once_and_never_stored_or_logged(sqlite_engine, caplog):
    caplog.set_level("DEBUG")
    code, out, err = run_bootstrap(sqlite_engine)
    password = temp_password(out)
    assert len(password) >= 24 and out.count(password) == 1
    assert password not in err and password not in caplog.text
    assert password not in dump_database(sqlite_engine)
    assert password not in all_users(sqlite_engine)[0].password_hash


def test_each_run_generates_a_different_password(tmp_path):
    from sqlalchemy import create_engine
    from alembic import command
    from tests.pg_support import alembic_config

    seen = set()
    for i in range(3):
        url = f"sqlite:///{tmp_path / f'db{i}.sqlite'}"
        command.upgrade(alembic_config(url), "head")
        seen.add(temp_password(run_bootstrap(create_engine(url))[1]))
    assert len(seen) == 3


def test_default_output_goes_to_stdout_only(sqlite_engine, capsys):
    code = run(ARGS, settings=dev_settings(), engine=sqlite_engine, allow_non_postgres=True)
    captured = capsys.readouterr()
    assert code == 0 and temp_password(captured.out) and captured.err == ""


# --- first login --------------------------------------------------------------------------------

def test_first_login_forces_a_password_change(sqlite_engine):
    password = temp_password(run_bootstrap(sqlite_engine)[1])
    client = build_client(sqlite_engine)
    response = login(client, EMAIL, password)
    assert response.status_code == 200 and response.json()["user"]["must_change_password"] is True
    headers = {"X-CSRF-Token": response.json()["csrf_token"]}

    blocked = client.get(f"{API}/users", headers=headers)
    assert blocked.status_code == 403 and blocked.json() == {"detail": "Password change required"}

    changed = client.post(f"{API}/auth/change-password", headers=headers,
                          json={"current_password": password, "new_password": NEW_PW})
    assert changed.status_code == 200
    again = login(client, EMAIL, NEW_PW)
    assert again.status_code == 200 and again.json()["user"]["must_change_password"] is False
    assert client.get(f"{API}/users").status_code == 200
    assert login(client, EMAIL, password).status_code == 401  # temporary password is now dead


# --- audit ---------------------------------------------------------------------------------------

def test_one_allow_listed_audit_event_with_an_intact_chain(sqlite_engine):
    code, out, _ = run_bootstrap(sqlite_engine)
    password = temp_password(out)
    with Session(sqlite_engine) as s:
        (event,) = list(s.scalars(select(AuditLog)))
        (user,) = list(s.scalars(select(User)))
        assert verify_chain(s).ok
    assert (event.action, event.outcome, event.entity_type, event.entity_id) == (
        "user.bootstrap_admin", "success", "user", str(user.id))
    assert event.actor_user_id is None and event.actor_role is None
    assert event.details == {"new_role": "ADMIN", "new_is_active": True, "reason": "development_bootstrap"}
    blob = json.dumps({c.name: str(getattr(event, c.name)) for c in AuditLog.__table__.columns})
    for secret in (password, user.password_hash, "argon2"):
        assert secret not in blob


def test_a_refused_run_writes_no_audit_event(sqlite_engine):
    run_bootstrap(sqlite_engine, settings=dev_settings(app_env="test"))
    with Session(sqlite_engine) as s:
        assert s.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_user_and_audit_event_are_atomic(sqlite_engine, monkeypatch):
    import app.bootstrap_admin as module

    def boom(*a, **kw):
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(module, "write_audit_event", boom)
    with Session(sqlite_engine) as s, pytest.raises(RuntimeError):
        create_first_admin(s, email=EMAIL, full_name="Admin")
    assert all_users(sqlite_engine) == []


# --- concurrency (PostgreSQL *_test database only) ----------------------------------------------

def test_simultaneous_bootstraps_create_exactly_one_administrator(pg_head):
    attempts = 6
    barrier = threading.Barrier(attempts)
    outcomes, unexpected = [], []

    def worker(i):
        try:
            barrier.wait()
            with Session(pg_head) as s:
                create_first_admin(s, email=f"admin{i}@example.invalid", full_name="Admin")
            outcomes.append("created")
        except AdminAlreadyExists:
            outcomes.append("refused")
        except Exception as exc:  # anything else is a failure of the safety property
            unexpected.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(attempts)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not unexpected and not any(t.is_alive() for t in threads)
    assert sorted(outcomes) == ["created"] + ["refused"] * (attempts - 1)
    with Session(pg_head) as s:
        assert s.scalar(select(func.count()).select_from(User).where(User.role == "ADMIN")) == 1
        assert s.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "user.bootstrap_admin")) == 1
        assert verify_chain(s).ok
