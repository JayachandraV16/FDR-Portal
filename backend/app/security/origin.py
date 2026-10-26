"""Origin/Referer validation for state-changing requests (defense in depth beside CSRF tokens).

Fails closed: a request carrying neither header, a malformed one, or the opaque "null" origin is
not trusted. The allowed set is exactly the configured CORS_ORIGINS; "*" is never honoured.
"""

from urllib.parse import urlsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_origin(value: str, *, allow_path: bool = False) -> str | None:
    """Return 'scheme://host[:port]' (lower-cased, default port dropped) or None if not an origin.

    A real origin has no path, query or fragment; `allow_path` relaxes that for Referer values.
    """
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname or parts.username or parts.password:
        return None
    if not allow_path and (parts.path not in ("", "/") or parts.query or parts.fragment):
        return None
    host = parts.hostname.lower()
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    if port is None or port == _DEFAULT_PORTS[scheme]:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def is_trusted_origin(origin: str | None, referer: str | None, allowed_origins: list[str]) -> bool:
    allowed = {o for o in (normalize_origin(a) for a in allowed_origins if a != "*") if o}
    if origin is not None:
        # Origin wins when present; the literal "null" (sandboxed/opaque contexts) never matches.
        candidate = normalize_origin(origin)
    elif referer:
        candidate = normalize_origin(referer, allow_path=True)  # path/query on a Referer are ignored
    else:
        return False
    return candidate is not None and candidate in allowed
