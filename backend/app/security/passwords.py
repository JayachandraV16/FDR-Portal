"""Argon2id password hashing, verification and the password-length policy.

Only length rules are enforced (min 12, max 128, not equal to the email). No complexity rules are
invented here. Passwords are never logged and never appear in exception messages.
"""

import functools
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128

# argon2-cffi's PasswordHasher is Argon2id with the library's current recommended parameters.
_hasher = PasswordHasher()


@dataclass(frozen=True)
class PasswordCheck:
    valid: bool
    # Set only when the password was correct and the stored hash uses outdated parameters.
    new_hash: str | None = None


class PasswordPolicyError(ValueError):
    pass


def validate_password_policy(password: str, email: str | None = None) -> None:
    """Raise PasswordPolicyError (message contains no part of the password) if not acceptable."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters")
    if email is not None and password.strip().lower() == email.strip().lower():
        raise PasswordPolicyError("Password must not be the same as the email address")


def generate_temporary_password() -> str:
    """One-time initial password for an admin-created account (24 URL-safe chars, 144 bits).

    It is shown to the administrator once, stored only as an Argon2id hash, and must never be
    logged or audited. The account is created with must_change_password=true.
    """
    import secrets

    return secrets.token_urlsafe(18)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


@functools.lru_cache
def _dummy_hash() -> str:
    # Verified against when the account does not exist, so "no such user" costs the same as
    # "wrong password". Derived from random data, not a real credential.
    import secrets

    return _hasher.hash(secrets.token_urlsafe(24))


def verify_password(stored_hash: str | None, password: str) -> PasswordCheck:
    """Check a login attempt. Always performs exactly one Argon2 verification.

    `stored_hash` is None for an unknown account; a dummy hash is verified instead so response
    time does not reveal whether the account exists. Over-long input is rejected without hashing it.
    """
    if stored_hash is None or len(password) > MAX_PASSWORD_LENGTH:
        _burn_dummy_verification()
        return PasswordCheck(False)
    try:
        _hasher.verify(stored_hash, password)
    except InvalidHashError:
        # Corrupt stored hash: treat as failure, with comparable cost.
        _burn_dummy_verification()
        return PasswordCheck(False)
    except VerificationError:  # includes VerifyMismatchError
        return PasswordCheck(False)
    new_hash = hash_password(password) if _hasher.check_needs_rehash(stored_hash) else None
    return PasswordCheck(True, new_hash)


def _burn_dummy_verification() -> None:
    try:
        _hasher.verify(_dummy_hash(), "not-the-password")
    except VerificationError:
        pass
