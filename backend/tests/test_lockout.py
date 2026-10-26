from datetime import UTC, datetime, timedelta

from app.models import User
from app.security.lockout import is_locked, register_failure, register_success
from tests.api_support import make_settings

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def fresh_user(**kw) -> User:
    return User(email="u@example.invalid", full_name="U", password_hash="x", role="ADMIN",
                failed_login_count=kw.get("count", 0), locked_until=kw.get("locked_until"))


def test_failures_below_threshold_only_count():
    s, u = make_settings(login_max_failures=3), fresh_user()
    assert register_failure(u, s, NOW) == (False, 1)
    assert register_failure(u, s, NOW) == (False, 2)
    assert u.failed_login_count == 2 and not is_locked(u, NOW)


def test_threshold_locks_and_resets_counter():
    s, u = make_settings(login_max_failures=3, login_lockout_minutes=15), fresh_user(count=2)
    assert register_failure(u, s, NOW) == (True, 3)
    assert u.failed_login_count == 0
    assert u.locked_until == NOW + timedelta(minutes=15)
    assert is_locked(u, NOW + timedelta(minutes=14, seconds=59))
    assert not is_locked(u, NOW + timedelta(minutes=15))


def test_success_clears_state():
    u = fresh_user(count=2, locked_until=NOW + timedelta(minutes=5))
    register_success(u, NOW)
    assert u.failed_login_count == 0 and u.locked_until is None and u.last_login_at == NOW


def test_naive_locked_until_from_sqlite_is_treated_as_utc():
    u = fresh_user(locked_until=(NOW + timedelta(minutes=1)).replace(tzinfo=None))
    assert is_locked(u, NOW)
