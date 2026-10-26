"""Session cookie attributes in one place so set and clear always agree.

HttpOnly is always on. SameSite and Secure come from Settings (Secure defaults to true and can only
be switched off outside production; see Settings validation).
"""

from fastapi import Response

from app.config import Settings

SESSION_COOKIE_NAME = "fdr_session"


def set_session_cookie(response: Response, raw_token: str, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        raw_token,
        max_age=settings.session_absolute_timeout_minutes * 60,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )
