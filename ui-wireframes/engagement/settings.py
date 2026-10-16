"""Configuration from environment / .env (pydantic-settings loads .env through python-dotenv)."""
from __future__ import annotations

import re
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ENGAGEMENT_", extra="ignore")

    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/postgres"
    db_schema: str = "engagement"
    log_level: str = "INFO"
    max_steps_per_journey: int = Field(100, ge=1)  # guards against runaway flows
    redis_url: str | None = None  # reserved for platform use (journey state, rate limits)

    @field_validator("db_schema")
    @classmethod
    def _identifier(cls, value: str) -> str:
        # The schema name is interpolated into SQL, so only plain identifiers are allowed.
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("db_schema must be a plain SQL identifier")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
