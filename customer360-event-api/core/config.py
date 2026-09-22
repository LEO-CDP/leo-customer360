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
    s3_verify_ssl: bool = Field(
        default=True,
        validation_alias=AliasChoices("S3_VERIFY_SSL", "s3_verify_ssl"),
    )
    s3_auto_create_buckets: bool = Field(
        default=True,
        validation_alias=AliasChoices("S3_AUTO_CREATE_BUCKETS", "s3_auto_create_buckets"),
    )
    event_schema_version: int = Field(
        default=1,
        validation_alias=AliasChoices("EVENT_SCHEMA_VERSION", "event_schema_version"),
    )
    event_ingestion_version: str = Field(
        default="1.0",
        validation_alias=AliasChoices(
            "EVENT_INGESTION_VERSION", "event_ingestion_version"
        ),
    )
    tracking_idempotency_ttl_seconds: int = Field(
        default=172800,
        validation_alias=AliasChoices(
            "TRACKING_IDEMPOTENCY_TTL_SECONDS", "tracking_idempotency_ttl_seconds"
        ),
    )
    tracking_processed_prefix: str = Field(
        default="_processed",
        validation_alias=AliasChoices(
            "TRACKING_PROCESSED_PREFIX", "tracking_processed_prefix"
        ),
    )
    event_max_object_size_bytes: int = Field(
        default=16 * 1024 * 1024,
        validation_alias=AliasChoices(
            "EVENT_MAX_OBJECT_SIZE_BYTES", "event_max_object_size_bytes"
        ),
    )
    max_request_body_bytes: int = Field(
        default=4 * 1024 * 1024,
        validation_alias=AliasChoices(
            "TRACKING_MAX_REQUEST_BODY_BYTES", "max_request_body_bytes"
        ),
    )
    max_events_per_request: int = Field(
        default=1000,
        validation_alias=AliasChoices("TRACKING_MAX_EVENTS_PER_REQUEST", "tracking_max_events_per_request"),
    )
    time_to_flush_log: int = Field(
        default=5,
        validation_alias=AliasChoices("TIME_TO_FLUSH_LOG", "time_to_flush_log"),
    )
    tracking_log_queue_max_size: int = Field(
        default=1000,
        validation_alias=AliasChoices(
            "TRACKING_LOG_QUEUE_MAX_SIZE", "tracking_log_queue_max_size"
        ),
    )
    tracking_log_flush_batch_size: int = Field(
        default=200,
        validation_alias=AliasChoices(
            "TRACKING_LOG_FLUSH_BATCH_SIZE", "tracking_log_flush_batch_size"
        ),
    )
    tracking_queue_backend: Literal["redis_stream", "memory"] = Field(
        default="redis_stream",
        validation_alias=AliasChoices("TRACKING_QUEUE_BACKEND", "tracking_queue_backend"),
    )
    tracking_stream_name: str = Field(
        default="data-tracking:events",
        validation_alias=AliasChoices("TRACKING_STREAM_NAME", "tracking_stream_name"),
    )
    tracking_stream_group: str = Field(
        default="s3-writers",
        validation_alias=AliasChoices("TRACKING_STREAM_GROUP", "tracking_stream_group"),
    )
    tracking_stream_max_length: int = Field(
        default=100000,
        validation_alias=AliasChoices(
            "TRACKING_STREAM_MAX_LENGTH", "tracking_stream_max_length"
        ),
    )
    tracking_stream_claim_idle_ms: int = Field(
        default=60000,
        validation_alias=AliasChoices(
            "TRACKING_STREAM_CLAIM_IDLE_MS", "tracking_stream_claim_idle_ms"
        ),
    )
    tracking_stream_block_ms: int = Field(
        default=1000,
        validation_alias=AliasChoices("TRACKING_STREAM_BLOCK_MS", "tracking_stream_block_ms"),
    )
    tracking_stream_retry_seconds: float = Field(
        default=1.0,
        validation_alias=AliasChoices(
            "TRACKING_STREAM_RETRY_SECONDS", "tracking_stream_retry_seconds"
        ),
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
        default="customer360-event-api",
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
    tracking_rate_limit_whitelist: str = Field(
        default="",
        validation_alias=AliasChoices(
            "TRACKING_RATE_LIMIT_WHITELIST", "tracking_rate_limit_whitelist"
        ),
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
    email_tracking_secret: str = Field(
        default="leocdp-dev-tracking-secret",
        validation_alias=AliasChoices("EMAIL_TRACKING_SECRET", "email_tracking_secret"),
    )
    email_webhook_signing_secret: str = Field(
        default="",
        validation_alias=AliasChoices(
            "CRM_EMAIL_WEBHOOK_SIGNING_SECRET", "email_webhook_signing_secret"
        ),
    )

settings = Settings()
