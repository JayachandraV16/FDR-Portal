from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    # No default on purpose: credentials must come from the environment.
    database_url: str
    cors_origins: list[str] = ["http://localhost:5173"]
    storage_dir: Path = Path("storage")
    llm_enabled: bool = False

    # Authentication. No default for secret_key: it must be supplied via the environment.
    secret_key: SecretStr = Field(min_length=32)
    # Engineering defaults, not AICTE policy.
    session_idle_timeout_minutes: int = Field(default=30, ge=1)
    session_absolute_timeout_minutes: int = Field(default=480, ge=1)
    login_max_failures: int = Field(default=5, ge=1)
    login_lockout_minutes: int = Field(default=15, ge=1)
    # Secure cookies by default; may only be disabled in development/test (see validator).
    cookie_secure: bool = True
    # The UI and API are assumed to be served from the same site (same origin, or the
    # Vite dev proxy). Strict would break a cross-site deployment.
    cookie_samesite: Literal["strict", "lax"] = "strict"

    @model_validator(mode="after")
    def _enforce_auth_safety(self) -> "Settings":
        if not self.cookie_secure and self.app_env == "production":
            raise ValueError("COOKIE_SECURE=false is not allowed when APP_ENV=production")
        if self.session_idle_timeout_minutes > self.session_absolute_timeout_minutes:
            raise ValueError("Idle timeout must not exceed the absolute session timeout")
        return self

    @field_validator("cors_origins")
    @classmethod
    def _origins_must_be_explicit(cls, v: list[str]) -> list[str]:
        # These origins double as the trusted set for CSRF Origin checks, so no wildcards.
        from app.security.origin import normalize_origin

        for origin in v:
            if normalize_origin(origin) is None:
                raise ValueError(
                    "CORS_ORIGINS entries must be explicit origins like http://localhost:5173 "
                    "(wildcards are not allowed)"
                )
        return v

    @field_validator("database_url")
    @classmethod
    def _use_psycopg_driver(cls, v: str) -> str:
        # Accept plain postgresql:// URLs but always use the psycopg (v3) driver.
        for prefix in ("postgresql://", "postgres://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix):]
        return v

    @field_validator("storage_dir")
    @classmethod
    def _resolve_storage_dir(cls, v: Path) -> Path:
        return v if v.is_absolute() else (BACKEND_DIR / v).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
