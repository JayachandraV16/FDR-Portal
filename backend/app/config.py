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
    # Engineering limits for uploaded documents, not AICTE policy.
    document_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    document_max_per_fdr: int = Field(default=20, ge=1, le=200)
    # Document extraction limits (engineering defaults). OCR is optional: unset OCR_COMMAND and the
    # server looks for `tesseract` on PATH; without either, scanned documents report OCR_UNAVAILABLE.
    extraction_max_pages: int = Field(default=10, ge=1, le=50)
    extraction_max_text_chars: int = Field(default=60_000, ge=1_000, le=500_000)
    extraction_timeout_seconds: float = Field(default=30.0, ge=2.0, le=300.0)
    extraction_max_image_pixels: int = Field(default=25_000_000, ge=100_000, le=100_000_000)
    ocr_command: str | None = None
    # Background extraction (jobs are rows in the database; workers are threads in the API process).
    extraction_workers: int = Field(default=2, ge=0, le=4)  # 0 disables workers: jobs then wait in the queue
    extraction_queue_limit: int = Field(default=20, ge=1, le=1000)  # pending + running jobs accepted at once
    extraction_max_attempts: int = Field(default=3, ge=1, le=10)  # claims before an interrupted job is failed
    extraction_poll_seconds: float = Field(default=2.0, ge=0.05, le=60.0)
    ocr_language: str = Field(default="eng", pattern=r"^[A-Za-z_]{3,12}(\+[A-Za-z_]{3,12}){0,3}$")
    llm_enabled: bool = False
    # FDR Copilot. By default the assistant answers from built-in help and the caller's own records; no
    # data leaves the server. An OpenAI-compatible provider can be enabled for wording, in which case ONLY the
    # question and non-sensitive facts (counts, statuses, dates, FDR numbers) are sent, never documents,
    # amounts or names. The key is a server-side secret and is never sent to the browser.
    chat_provider: str = "none"  # "none", "openai_compatible" or "gemini"; anything else falls back to the built-in assistant
    chat_base_url: str | None = None
    chat_api_key: SecretStr | None = None
    chat_model: str | None = None
    chat_timeout_seconds: float = Field(default=15.0, ge=1.0, le=60.0)
    chat_max_message_chars: int = Field(default=1000, ge=50, le=4000)

    # Reviewer workload dashboard. There is no official review deadline in this system, so none is assumed: unless
    # REVIEW_SLA_DAYS is set, the dashboard shows elapsed waiting time only and reports no SLA breaches.
    review_sla_days: int | None = Field(default=None, ge=1, le=365)

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

    @property
    def chat_provider_problem(self) -> str | None:
        """Why the configured AI provider cannot be used, or None if it is fine (or deliberately off).

        A problem never stops the API from starting: the assistant simply stays on its built-in answers. The text
        is fixed and never contains a configured value, so it is safe to log and to show."""
        name = (self.chat_provider or "none").strip().lower()
        if name in ("", "none"):
            return None
        if name not in ("openai_compatible", "gemini"):
            return "CHAT_PROVIDER is not a supported provider"
        if not self.chat_model or not self.chat_api_key or not self.chat_api_key.get_secret_value().strip():
            return "CHAT_MODEL and CHAT_API_KEY are both required"
        if name == "openai_compatible" and not self.chat_base_url:
            return "CHAT_BASE_URL is required for this provider"
        if self.chat_base_url and not self.chat_base_url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            return "CHAT_BASE_URL must use https (plain http is only allowed for localhost)"
        return None

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
