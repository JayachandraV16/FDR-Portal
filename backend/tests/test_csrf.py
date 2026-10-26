import hmac
import uuid

from app.security import csrf
from app.security.csrf import generate_csrf_token, validate_csrf_token

SECRET = "s" * 40
SID = uuid.uuid4()


def test_token_validates_for_its_session():
    assert validate_csrf_token(generate_csrf_token(SID, SECRET), SID, SECRET) is True


def test_token_is_deterministic_and_url_safe():
    t = generate_csrf_token(SID, SECRET)
    assert t == generate_csrf_token(SID, SECRET)
    assert set(t) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def test_token_is_bound_to_session_and_secret():
    t = generate_csrf_token(SID, SECRET)
    assert validate_csrf_token(t, uuid.uuid4(), SECRET) is False
    assert validate_csrf_token(t, SID, "o" * 40) is False


def test_missing_tampered_or_odd_tokens_are_rejected():
    t = generate_csrf_token(SID, SECRET)
    for bad in (None, "", t[:-1], t + "A", "x" * 500, "ünïcödé-token", t.swapcase()):
        assert validate_csrf_token(bad, SID, SECRET) is False


def test_comparison_uses_compare_digest(monkeypatch):
    calls = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append(1)
        return real(a, b)

    monkeypatch.setattr(csrf.hmac, "compare_digest", spy)
    validate_csrf_token(generate_csrf_token(SID, SECRET), SID, SECRET)
    assert calls
