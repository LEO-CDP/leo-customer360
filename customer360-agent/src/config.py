"""Agent-service settings. Self-contained: this service does NOT depend on the
customer360 DAO -- it only needs LLM config + the prompt-store DB URL, read from
env / .env."""

from typing import Any

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The use-case agents that can be given their own model (LLM_USE_CASE_MODELS).
USE_CASES = ("sql", "narrative", "support", "tickets", "schema_mapping", "copy", "orchestrator", "persona")


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
    # Postgres schema holding the prompt-store tables (created by customer360-database).
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
    # Optional per-use-case model, e.g. {"sql": "openai/gpt-4o", "tickets": "openai/gpt-4o-mini"}.
    # A use case without an entry uses llm_model. Keys must be names from USE_CASES.
    llm_use_case_models: dict[str, str] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("LLM_USE_CASE_MODELS", "llm_use_case_models"),
    )

    @field_validator("llm_use_case_models")
    @classmethod
    def _known_use_cases(cls, value: dict[str, str]) -> dict[str, str]:
        unknown = sorted(set(value) - set(USE_CASES))
        if unknown:
            raise ValueError(f"unknown use case(s) in LLM_USE_CASE_MODELS: {unknown}; allowed: {list(USE_CASES)}")
        return value

    def model_for(self, use_case: str) -> str:
        """The model string for one use-case agent: its override, else llm_model."""
        if use_case not in USE_CASES:
            raise KeyError(use_case)
        return self.llm_use_case_models.get(use_case) or self.llm_model

    # ASGI root_path when served behind a path-stripping proxy (Caddy handle_path
    # /agent/*). Set to "/agent" so Swagger + /openapi.json resolve under the public
    # prefix; blank when reached directly on :8009.
    root_path: str = Field(
        default="",
        validation_alias=AliasChoices("AGENT_ROOT_PATH", "ROOT_PATH", "root_path"),
    )


settings = Settings()
