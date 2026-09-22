"""Runtime settings for the tracking-log analytics service."""

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


def _int_setting(name: str, default: str) -> int:
    return int(os.environ.get(name, default))


def _positive_int_setting(name: str, default: str) -> int:
    return max(1, _int_setting(name, default))


@dataclass(frozen=True, slots=True)
class AnalyticsSettings:
    """Immutable settings shared by the analytics collaborators."""

    db_host: str
    db_name: str
    db_user: str
    db_password: str
    db_port: str
    db_schema: str
    data_source_limit: int
    max_workers: int
    source_batch_size: int
    object_batch_size: int
    redis_host: str
    redis_port: int
    redis_db: int
    redis_password: Optional[str]
    s3_endpoint_url: Optional[str]
    s3_region: str
    s3_access_key_id: Optional[str]
    s3_secret_access_key: Optional[str]
    s3_session_token: Optional[str]
    s3_force_path_style: bool
    s3_verify_ssl: bool
    s3_max_pool_connections: int
    analytics_lock_key: str
    analytics_lock_ttl_seconds: int
    event_raw_prefix: str
    source_lock_prefix: str
    source_state_prefix: str
    source_daily_prefix: str
    source_profile_hll_prefix: str
    source_profile_analytics_prefix: str
    source_profile_event_prefix: str
    lock_ttl_seconds: int
    processed_object_ttl_seconds: int

    @classmethod
    def from_environment(cls) -> "AnalyticsSettings":
        """Load and validate settings from the process environment."""
        return cls(
            db_host=os.environ.get("DB_HOST", "localhost"),
            db_name=os.environ.get("DB_NAME", "customer360"),
            db_user=os.environ.get("DB_USER", "postgres"),
            db_password=os.environ.get("DB_PASSWORD", "postgres"),
            db_port=os.environ.get("DB_PORT", "5432"),
            db_schema=os.environ.get("DB_SCHEMA", "customer360"),
            data_source_limit=_int_setting("ANALYTICS_DATA_SOURCE_LIMIT", "0"),
            max_workers=_positive_int_setting("ANALYTICS_MAX_WORKERS", "2"),
            source_batch_size=_positive_int_setting("ANALYTICS_SOURCE_BATCH_SIZE", "16"),
            object_batch_size=_positive_int_setting("ANALYTICS_OBJECT_BATCH_SIZE", "500"),
            redis_host=os.environ.get("REDIS_HOST", "localhost"),
            redis_port=_int_setting("REDIS_PORT", "6580"),
            redis_db=_int_setting("REDIS_DB", "0"),
            redis_password=os.environ.get("REDIS_PASSWORD"),
            s3_endpoint_url=os.environ.get("ANALYTICS_S3_ENDPOINT_URL")
            or os.environ.get("S3_ENDPOINT_URL"),
            s3_region=os.environ.get("S3_REGION", "us-east-1"),
            s3_access_key_id=os.environ.get("S3_ACCESS_KEY_ID"),
            s3_secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY"),
            s3_session_token=os.environ.get("S3_SESSION_TOKEN"),
            s3_force_path_style=os.environ.get("S3_FORCE_PATH_STYLE", "false").lower()
            == "true",
            s3_verify_ssl=os.environ.get("S3_VERIFY_SSL", "true").lower() == "true",
            s3_max_pool_connections=_int_setting("ANALYTICS_S3_MAX_POOL_CONNECTIONS", "64"),
            analytics_lock_key="analytics:tracking-log-run-lock",
            analytics_lock_ttl_seconds=_int_setting(
                "ANALYTICS_RUN_LOCK_TTL_SECONDS", "3600"
            ),
            event_raw_prefix=os.environ.get("ANALYTICS_EVENT_RAW_PREFIX", "events").strip(
                "/"
            ),
            source_lock_prefix="analytics:data-source-lock:",
            source_state_prefix="analytics:data-source-state:",
            source_daily_prefix="analytics:data-source-daily:",
            source_profile_hll_prefix="analytics:data-source-profiles-hll:",
            source_profile_analytics_prefix="analytics:data-source-profile-analytics:",
            source_profile_event_prefix="analytics:data-source-profile-event:",
            lock_ttl_seconds=_int_setting("ANALYTICS_LOCK_TTL_SECONDS", "3600"),
            processed_object_ttl_seconds=_int_setting(
                "ANALYTICS_PROCESSED_OBJECT_TTL_SECONDS", str(48 * 60 * 60)
            ),
        )