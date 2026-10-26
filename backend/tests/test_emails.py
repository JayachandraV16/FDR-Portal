import pytest

from app.security.emails import InvalidEmail, normalize_email


def test_normalises_case_and_whitespace():
    assert normalize_email("  Admin@Example.INVALID ") == "admin@example.invalid"


def test_nfkc_folds_compatibility_characters():
    assert normalize_email("ＡＢＣ@example.invalid") == "abc@example.invalid"


@pytest.mark.parametrize(
    "bad", ["", "no-at-sign", "a@b", "a b@c.d", "a@@c.d", "@c.d", "a@" + "x" * 250 + ".com"]
)
def test_rejects_invalid(bad):
    with pytest.raises(InvalidEmail):
        normalize_email(bad)
