"""Agent-service settings. Self-contained: this service does NOT depend on the
customer360 DAO -- it only needs LLM config + the prompt-store DB URL, read from
env / .env."""

from typing import Any

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_version: str = Field(
        default="0.1.0",
        validation_alias=AliasChoices("AGENT_API_VERSION", "api_version"),
    )

    # Shared bearer token gating /plan/* (customer360-api sends it). Blank = auth
    # DISABLED (local dev only) — a warning is logged at startup. /health is always open.
    api_token: str = Field(
        default="",
        validation_alias=AliasChoices("AGENT_API_TOKEN", "api_token"),
    )

    # SQLAlchemy URL for the Postgres-backed prompt store, e.g.
    # postgresql+psycopg2://user:pass@host:5432/db.
    database_url: str = Field(
        default="",
        validation_alias=AliasChoices("AGENT_DATABASE_URL", "DATABASE_URL", "database_url"),
    )
    # Postgres schema holding the prompt-store tables (created by database-init).
    db_schema: str = Field(
        default="customer360",
        validation_alias=AliasChoices("AGENT_DB_SCHEMA", "DB_SCHEMA", "db_schema"),
    )

    # --- LLM (LiteLLM) -----------------------------------------------------
    # One provider-agnostic triple. The model string carries the provider:
    #   gemini/gemini-2.5-flash | openai/gpt-4o | anthropic/claude-3-5-sonnet-latest
    #   openai/<name> + LLM_BASE_URL  ->  any local OpenAI-compatible runtime (key blank)
    llm_model: str = Field(
        default="gemini/gemini-2.5-flash",
        validation_alias=AliasChoices("LLM_MODEL", "AI_MODEL", "llm_model"),
    )
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("LLM_API_KEY", "AI_API_KEY", "llm_api_key"),
    )
    # Blank for a hosted provider's default endpoint; set for a local/self-hosted LLM.
    llm_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("LLM_BASE_URL", "OPENAI_BASE_URL", "llm_base_url"),
    )
    # Extra params forwarded verbatim to every litellm.completion() call
    # (e.g. {"max_tokens": 2048, "timeout": 60}). JSON object; a per-request
    # extra_config is merged on top of this.
    llm_extra_config: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("LLM_EXTRA_CONFIG", "llm_extra_config"),
    )


settings = Settings()
