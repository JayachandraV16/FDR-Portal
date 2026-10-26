import pytest
from argon2 import PasswordHasher

from app.security import passwords
from app.security.passwords import (
    PasswordPolicyError,
    hash_password,
    validate_password_policy,
    verify_password,
)

PW = "correct horse battery staple"


def test_hash_is_argon2id_and_salted():
    h1, h2 = hash_password(PW), hash_password(PW)
    assert h1.startswith("$argon2id$")
    assert h1 != h2
    assert PW not in h1


def test_verify_correct_and_wrong_password():
    h = hash_password(PW)
    assert verify_password(h, PW).valid is True
    assert verify_password(h, PW + "x").valid is False
    assert verify_password(h, "").valid is False


def test_verify_returns_no_rehash_for_current_parameters():
    assert verify_password(hash_password(PW), PW).new_hash is None


def test_outdated_parameters_trigger_rehash_only_on_correct_password():
    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(PW)
    wrong = verify_password(weak, "not the password")
    assert wrong.valid is False and wrong.new_hash is None
    ok = verify_password(weak, PW)
    assert ok.valid is True and ok.new_hash is not None
    assert ok.new_hash != weak
    assert verify_password(ok.new_hash, PW) == passwords.PasswordCheck(True, None)


class SpyHasher:
    """PasswordHasher uses __slots__, so wrap it instead of patching its methods."""

    def __init__(self, real):
        self._real = real
        self.verified: list[tuple[str, str]] = []

    def verify(self, h, p):
        self.verified.append((h, p))
        return self._real.verify(h, p)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_unknown_user_still_performs_a_verification(monkeypatch):
    spy = SpyHasher(passwords._hasher)
    monkeypatch.setattr(passwords, "_hasher", spy)
    result = verify_password(None, PW)
    calls = [h for h, _ in spy.verified]
    assert result.valid is False and result.new_hash is None
    assert len(calls) == 1 and calls[0].startswith("$argon2id$")


def test_corrupt_stored_hash_is_a_failure_not_an_exception():
    assert verify_password("not-an-argon2-hash", PW).valid is False


def test_overlong_password_is_rejected_without_hashing_it(monkeypatch):
    h = hash_password(PW)
    spy = SpyHasher(passwords._hasher)
    monkeypatch.setattr(passwords, "_hasher", spy)
    assert verify_password(h, "a" * 10_000).valid is False
    assert spy.verified and all(len(p) < 100 for _, p in spy.verified)  # huge input never reached Argon2


def test_unicode_passwords_work():
    pw = "pässwörd-日本語-long"
    assert verify_password(hash_password(pw), pw).valid is True


@pytest.mark.parametrize("pw", ["short", "a" * 11])
def test_policy_rejects_short(pw):
    with pytest.raises(PasswordPolicyError):
        validate_password_policy(pw)


def test_policy_rejects_overlong_and_email_equal():
    with pytest.raises(PasswordPolicyError):
        validate_password_policy("a" * 129)
    with pytest.raises(PasswordPolicyError):
        validate_password_policy("Admin@Example.invalid", "admin@example.invalid")


def test_policy_accepts_boundaries_and_error_does_not_echo_password():
    validate_password_policy("a" * 12)
    validate_password_policy("a" * 128)
    secret = "s3cr3t!"
    with pytest.raises(PasswordPolicyError) as exc:
        validate_password_policy(secret)
    assert secret not in str(exc.value)
