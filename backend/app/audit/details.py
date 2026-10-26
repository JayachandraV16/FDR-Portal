"""Allow-listed sanitisation of audit `details`.

Anything not explicitly allowed is rejected loudly (AuditDetailError) rather than silently
dropped, so a developer adding a field sees the failure in tests instead of losing evidence or
leaking data. Secrets can never be allow-listed: key names containing a forbidden fragment are
refused even if someone adds them to ALLOWED_DETAIL_KEYS (checked at import time).
"""

from typing import Any

MAX_STRING_LENGTH = 200
MAX_LIST_ITEMS = 20

# key -> permitted value kind. "str_list" is a list of short strings.
ALLOWED_DETAIL_KEYS: dict[str, str] = {
    "reason": "str",  # machine-readable reason code, e.g. "bad_credentials"
    "http_method": "str",
    "route": "str",  # route template, e.g. "/api/v1/institutions/{id}"
    "status_code": "int",
    "target_user_id": "str",
    "target_institution_id": "str",
    "target_fdr_id": "str",
    "old_role": "str",
    "new_role": "str",
    "old_is_active": "bool",
    "new_is_active": "bool",
    "old_institution_id": "str",
    "new_institution_id": "str",
    "changed_fields": "str_list",  # field NAMES only, never values
    "required_roles": "str_list",
    "revoked_count": "int",
    "result_count": "int",
    "filters": "str_list",  # filter NAMES only
    "failed_login_count": "int",
    "locked": "bool",
}

# Matched case-insensitively against key names.
FORBIDDEN_KEY_FRAGMENTS = (
    "password", "passwd", "secret", "token", "cookie", "csrf", "hash",
    "authorization", "credential", "session", "api_key", "apikey", "private",
)


class AuditDetailError(ValueError):
    pass


def _forbidden(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in FORBIDDEN_KEY_FRAGMENTS)


# Fail at import if the allow-list itself ever names a secret-bearing field.
_bad = [k for k in ALLOWED_DETAIL_KEYS if _forbidden(k)]
if _bad:  # pragma: no cover - guards future edits
    raise RuntimeError(f"Forbidden audit detail keys in allow-list: {_bad}")


def _check_str(key: str, value: Any) -> str:
    if not isinstance(value, str):
        raise AuditDetailError(f"Audit detail '{key}' must be a string")
    if len(value) > MAX_STRING_LENGTH:
        raise AuditDetailError(f"Audit detail '{key}' is too long")
    return value


def sanitize_details(details: dict[str, Any] | None) -> dict[str, Any] | None:
    """Validate against the allow-list and return a plain, JSON-safe copy."""
    if details is None:
        return None
    if not isinstance(details, dict):
        raise AuditDetailError("Audit details must be a dict")
    clean: dict[str, Any] = {}
    for key, value in details.items():
        if _forbidden(str(key)):
            raise AuditDetailError(f"Audit detail key '{key}' is forbidden")
        kind = ALLOWED_DETAIL_KEYS.get(key)
        if kind is None:
            raise AuditDetailError(f"Audit detail key '{key}' is not allow-listed")
        if kind == "str":
            clean[key] = _check_str(key, value)
        elif kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise AuditDetailError(f"Audit detail '{key}' must be an integer")
            clean[key] = value
        elif kind == "bool":
            if not isinstance(value, bool):
                raise AuditDetailError(f"Audit detail '{key}' must be a boolean")
            clean[key] = value
        elif kind == "str_list":
            if not isinstance(value, (list, tuple)) or len(value) > MAX_LIST_ITEMS:
                raise AuditDetailError(f"Audit detail '{key}' must be a short list of strings")
            clean[key] = [_check_str(key, item) for item in value]
    return clean
