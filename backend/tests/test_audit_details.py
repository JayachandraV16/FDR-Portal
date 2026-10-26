import pytest

from app.audit import AuditDetailError, sanitize_details
from app.audit.details import ALLOWED_DETAIL_KEYS, FORBIDDEN_KEY_FRAGMENTS


def test_none_passes_through():
    assert sanitize_details(None) is None


def test_allowed_keys_and_types_pass_and_are_copied():
    original = {
        "reason": "bad_credentials",
        "status_code": 401,
        "locked": False,
        "changed_fields": ["role", "is_active"],
    }
    clean = sanitize_details(original)
    assert clean == original and clean is not original
    assert clean["changed_fields"] is not original["changed_fields"]


@pytest.mark.parametrize(
    "key",
    ["password", "new_password", "token", "session_token", "csrf_token", "secret_key",
     "cookie", "password_hash", "authorization", "api_key", "Set-Cookie"],
)
def test_secret_bearing_keys_are_refused_with_a_forbidden_error(key):
    with pytest.raises(AuditDetailError, match="forbidden"):
        sanitize_details({key: "whatever"})


def test_unknown_keys_are_rejected_not_silently_dropped():
    with pytest.raises(AuditDetailError, match="not allow-listed"):
        sanitize_details({"surprise": "x"})


@pytest.mark.parametrize(
    "bad",
    [
        {"status_code": "401"},
        {"status_code": True},
        {"locked": "yes"},
        {"reason": 5},
        {"reason": "x" * 201},
        {"reason": {"nested": "dict"}},
        {"changed_fields": "role"},
        {"changed_fields": [1, 2]},
        {"changed_fields": ["x"] * 21},
        {"changed_fields": [["nested"]]},
    ],
)
def test_wrong_types_and_oversize_values_are_rejected(bad):
    with pytest.raises(AuditDetailError):
        sanitize_details(bad)


def test_non_dict_rejected():
    with pytest.raises(AuditDetailError):
        sanitize_details(["reason"])  # type: ignore[arg-type]


def test_allow_list_itself_contains_no_secret_bearing_key():
    for key in ALLOWED_DETAIL_KEYS:
        assert not any(f in key.lower() for f in FORBIDDEN_KEY_FRAGMENTS), key


def test_error_message_does_not_echo_values():
    with pytest.raises(AuditDetailError) as exc:
        sanitize_details({"password": "hunter2-hunter2"})
    assert "hunter2" not in str(exc.value)
