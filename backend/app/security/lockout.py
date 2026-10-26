"""Failed-login counter and temporary lockout.

These functions only mutate the in-memory User; callers must hold a row lock on that user
(SELECT ... FOR UPDATE) for the whole read-modify-commit cycle so concurrent attempts cannot
race past the threshold. When a lockout starts the counter resets to 0, so the user gets a fresh
set of attempts once the lock expires. Thresholds are engineering defaults, not AICTE policy.
"""

from datetime import datetime, timedelta

from app.config import Settings
from app.models import User
from app.timeutil import as_utc


def is_locked(user: User, now: datetime) -> bool:
    return user.locked_until is not None and as_utc(user.locked_until) > as_utc(now)


def register_failure(user: User, settings: Settings, now: datetime) -> tuple[bool, int]:
    """Count one failed attempt. Returns (lockout_started, attempts_counted)."""
    attempts = user.failed_login_count + 1
    if attempts >= settings.login_max_failures:
        user.locked_until = as_utc(now) + timedelta(minutes=settings.login_lockout_minutes)
        user.failed_login_count = 0
        return True, attempts
    user.failed_login_count = attempts
    return False, attempts


def register_success(user: User, now: datetime) -> None:
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = as_utc(now)
