import re
import unicodedata

MAX_EMAIL_LENGTH = 254
# Deliberately simple: one "@", no whitespace, a dotted domain. Deliverability is not checked.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class InvalidEmail(ValueError):
    pass


def normalize_email(raw: str) -> str:
    """Canonical form used for storage and lookup: NFKC, trimmed, lower-cased."""
    email = unicodedata.normalize("NFKC", raw).strip().lower()
    if len(email) > MAX_EMAIL_LENGTH or not _EMAIL_RE.match(email):
        raise InvalidEmail("Invalid email address")
    return email
