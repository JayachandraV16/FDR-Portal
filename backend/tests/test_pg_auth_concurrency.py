"""Concurrency tests that need real row locks (PostgreSQL only; skipped without TEST_DATABASE_URL)."""

import threading
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import verify_chain
from app.models import AuthSession, User
from tests.api_support import PW, audit_events, audit_rows, build_client, create_user, login, make_settings

EMAIL = "admin@example.invalid"


def _run_concurrently(n, fn):
    barrier = threading.Barrier(n)
    results, errors = [None] * n, []

    def worker(i):
        try:
            barrier.wait()
            results[i] = fn(i)
        except Exception as exc:  # surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not errors and not any(t.is_alive() for t in threads)
    return results


def test_concurrent_failed_logins_cannot_exceed_the_lockout_threshold(pg_head):
    create_user(pg_head)
    settings = make_settings(login_max_failures=5, login_lockout_minutes=15)
    clients = [build_client(pg_head, settings) for _ in range(20)]

    statuses = _run_concurrently(20, lambda i: login(clients[i], password="wrong-password-1").status_code)

    assert set(statuses) == {401}
    reasons = Counter(e.details["reason"] for e in audit_events(pg_head, "auth.login"))
    # Row locking serialises the attempts: exactly 5 are counted as bad passwords, one of them
    # starts the lockout, and every later attempt is turned away as locked. Without the lock,
    # several threads would read the same counter and more than 5 would be counted.
    assert reasons == {"bad_password": 5, "account_locked": 15}
    assert len(audit_events(pg_head, "auth.lockout")) == 1
    with Session(pg_head) as s:
        user = s.scalars(select(User)).one()
        assert user.locked_until is not None and user.failed_login_count == 0
        assert verify_chain(s).ok
        assert s.scalars(select(AuthSession)).all() == []


def test_correct_password_cannot_slip_through_while_failures_race_the_threshold(pg_head):
    create_user(pg_head)
    settings = make_settings(login_max_failures=3, login_lockout_minutes=15)
    clients = [build_client(pg_head, settings) for _ in range(12)]

    def attempt(i):
        # Mix wrong and correct attempts; whichever commits first decides, but the invariants hold.
        return login(clients[i], password=PW if i % 4 == 3 else "wrong-password-1").status_code

    statuses = _run_concurrently(12, attempt)
    assert set(statuses) <= {200, 401}

    with Session(pg_head) as s:
        user = s.scalars(select(User)).one()
        sessions = s.scalars(select(AuthSession)).all()
        # Never more sessions than successful logins, and the counter never exceeds the threshold.
        assert len(sessions) == statuses.count(200)
        assert 0 <= user.failed_login_count < settings.login_max_failures
        assert verify_chain(s).ok
    successes = audit_events(pg_head, "auth.login", "success")
    assert len(successes) == statuses.count(200)
    assert all(r.outcome in ("success", "failure", "denied") for r in audit_rows(pg_head))


def test_concurrent_successful_logins_create_distinct_sessions_and_a_valid_chain(pg_head):
    create_user(pg_head)
    clients = [build_client(pg_head) for _ in range(8)]
    statuses = _run_concurrently(8, lambda i: login(clients[i]).status_code)
    assert statuses == [200] * 8
    with Session(pg_head) as s:
        hashes = {x.token_hash for x in s.scalars(select(AuthSession))}
        assert len(hashes) == 8
        assert verify_chain(s).checked == 8
