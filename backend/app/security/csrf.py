"""CSRF tokens bound to a session.

token = base64url(HMAC-SHA256(SECRET_KEY, "fdr-csrf-v1:" + session_id)). It is computed on demand,
never stored, and handed to the SPA by login and /auth/me; the SPA echoes it in X-CSRF-Token.
A cross-site attacker cannot read it (same-origin policy) nor compute it (no SECRET_KEY).
"""

import base64
import hashlib
import hmac
import uuid

_LABEL = b"fdr-csrf-v1:"


def generate_csrf_token(session_id: uuid.UUID, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), _LABEL + session_id.bytes, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


def validate_csrf_token(provided: str | None, session_id: uuid.UUID, secret: str) -> bool:
    if not provided or len(provided) > 128:
        return False
    expected = generate_csrf_token(session_id, secret)
    # Compare as bytes so non-ASCII input cannot raise; compare_digest is constant-time.
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("ascii"))
