"""Environment-backed settings for the data-tracking service."""

from typing import Literal, Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = Field(
        default="development",
        validation_alias=AliasChoices("ENVIRONMENT", "environment"),
    )
    api_version: str = Field(
        default="1.0.0",
        validation_alias=AliasChoices("API_VERSION", "api_version"),
    )
    object_storage_mode: Literal["minio", "s3"] = Field(
        default="s3",
        validation_alias=AliasChoices("OBJECT_STORAGE_MODE", "object_storage_mode"),
    )
    s3_endpoint_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("S3_ENDPOINT_URL", "s3_endpoint_url"),
    )
    s3_region: str = Field(
        default="us-east-1",
        validation_alias=AliasChoices("S3_REGION", "s3_region"),
    )
    s3_access_key_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("S3_ACCESS_KEY_ID", "s3_access_key_id"),
    )
    s3_secret_access_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("S3_SECRET_ACCESS_KEY", "s3_secret_access_key"),
    )
    s3_session_token: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("S3_SESSION_TOKEN", "s3_session_token"),
    )
    s3_force_path_style: bool = Field(
        default=False,
        validation_alias=AliasChoices("S3_FORCE_PATH_STYLE", "s3_force_path_style"),
    )
    s3_auto_create_buckets: bool = Field(
        default=True,
        validation_alias=AliasChoices("S3_AUTO_CREATE_BUCKETS", "s3_auto_create_buckets"),
    )
    max_events_per_request: int = Field(
        default=1000,
        validation_alias=AliasChoices("TRACKING_MAX_EVENTS_PER_REQUEST", "tracking_max_events_per_request"),
    )
    redis_host: str = Field(
        default="localhost",
        validation_alias=AliasChoices("REDIS_HOST", "redis_host"),
    )
    redis_port: int = Field(
        default=6580,
        validation_alias=AliasChoices("REDIS_PORT", "redis_port"),
    )
    redis_db: int = Field(
        default=0,
        validation_alias=AliasChoices("REDIS_DB", "redis_db"),
    )
    redis_password: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("REDIS_PASSWORD", "redis_password"),
    )
    tracking_redis_key_prefix: str = Field(
        default="data-tracking-api",
        validation_alias=AliasChoices("TRACKING_REDIS_KEY_PREFIX", "tracking_redis_key_prefix"),
    )
    tracking_session_ttl_seconds: int = Field(
        default=86400,
        validation_alias=AliasChoices("TRACKING_SESSION_TTL_SECONDS", "tracking_session_ttl_seconds"),
    )
    tracking_rate_limit_requests: int = Field(
        default=120,
        validation_alias=AliasChoices("TRACKING_RATE_LIMIT_REQUESTS", "tracking_rate_limit_requests"),
    )
    tracking_rate_limit_window_seconds: int = Field(
        default=60,
        validation_alias=AliasChoices(
            "TRACKING_RATE_LIMIT_WINDOW_SECONDS", "tracking_rate_limit_window_seconds"
        ),
    )
    tracking_rate_limit_fail_open: bool = Field(
        default=True,
        validation_alias=AliasChoices("TRACKING_RATE_LIMIT_FAIL_OPEN", "tracking_rate_limit_fail_open"),
    )
    tracking_bot_filter_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("TRACKING_BOT_FILTER_ENABLED", "tracking_bot_filter_enabled"),
    )
    tracking_bot_user_agent_patterns: str = Field(
        default="googlebot,bingbot,google-inspectiontool,ahrefsbot,semrushbot,petalbot",
        validation_alias=AliasChoices(
            "TRACKING_BOT_USER_AGENT_PATTERNS", "tracking_bot_user_agent_patterns"
        ),
    )
    # Phase-1 event broker: when enabled, each accepted event is also XADD-ed to a Redis
    # Stream that the backend-system event Loader consumes (deployments/docs/
    # web-tracking-implementation-plan.md §6-8). Best-effort — the durable record is the S3
    # object, so a Redis hiccup never fails ingestion. OFF by default (dev/compose write only
    # to S3); the tracking-box deploy sets TRACKING_STREAM_ENABLED=true (its own broker Redis).
    tracking_stream_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("TRACKING_STREAM_ENABLED", "tracking_stream_enabled"),
    )
    tracking_stream_key: str = Field(
        default="cdp:events:raw",
        validation_alias=AliasChoices("TRACKING_STREAM_KEY", "tracking_stream_key"),
    )
    tracking_stream_maxlen: int = Field(
        default=1_000_000,
        validation_alias=AliasChoices("TRACKING_STREAM_MAXLEN", "tracking_stream_maxlen"),
    )


settings = Settings()
