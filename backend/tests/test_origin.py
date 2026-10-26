import pytest

from app.security.origin import is_trusted_origin, normalize_origin

ALLOWED = ["http://localhost:5173", "https://portal.example.invalid"]


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:5173",
        "HTTP://LOCALHOST:5173",
        "https://portal.example.invalid",
        "https://portal.example.invalid:443",
    ],
)
def test_allowed_origin_header(origin):
    assert is_trusted_origin(origin, None, ALLOWED) is True


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:5174",  # wrong port
        "http://localhost",  # default port differs from 5173
        "https://localhost:5173",  # wrong scheme
        "http://evil.example",
        "http://localhost:5173.evil.example",
        "https://portal.example.invalid.evil.example",
        "null",
        "",
        "not a url",
        "ftp://localhost:5173",
        "http://user:pw@localhost:5173",
    ],
)
def test_untrusted_origin_header(origin):
    assert is_trusted_origin(origin, None, ALLOWED) is False


def test_missing_both_headers_fails_closed():
    assert is_trusted_origin(None, None, ALLOWED) is False
    assert is_trusted_origin(None, "", ALLOWED) is False


def test_referer_is_used_only_when_origin_absent_and_path_is_ignored():
    assert is_trusted_origin(None, "http://localhost:5173/some/page?x=1", ALLOWED) is True
    assert is_trusted_origin(None, "http://evil.example/http://localhost:5173/", ALLOWED) is False


def test_origin_takes_precedence_over_referer():
    assert is_trusted_origin("http://evil.example", "http://localhost:5173/", ALLOWED) is False


def test_wildcard_in_allowed_list_is_never_honoured():
    assert is_trusted_origin("http://evil.example", None, ["*"]) is False
    assert is_trusted_origin("http://evil.example", None, []) is False


def test_normalize_origin():
    assert normalize_origin("HTTPS://Example.COM:443") == "https://example.com"
    assert normalize_origin("https://example.com/") == "https://example.com"
    assert normalize_origin("https://example.com/path") is None  # not an origin
    assert normalize_origin("https://example.com/path", allow_path=True) == "https://example.com"
    assert normalize_origin("http://[::1]:8000") == "http://[::1]:8000"
    assert normalize_origin("*") is None
