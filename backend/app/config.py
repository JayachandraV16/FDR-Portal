from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
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
