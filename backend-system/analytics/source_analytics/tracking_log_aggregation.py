"""Public compatibility facade for tracking-log analytics."""

import logging
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable, Optional

_BACKEND_SYSTEM_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _BACKEND_SYSTEM_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_SYSTEM_ROOT)

from .clients import AnalyticsClientFactory  # noqa: E402
from .config import AnalyticsSettings  # noqa: E402
from .event_records import (  # noqa: E402
    EVENT_CATEGORIES,
    EventEnvelopeError,
    EventRecordService,
)
from .metrics import AnalyticsMetrics  # noqa: E402
from .object_store import HOURLY_FOLDER_PATTERN, S3EventStore  # noqa: E402
from .repositories import AnalyticsRepository  # noqa: E402
from .source_state import (  # noqa: E402
    INCREMENT_IF_NEW_SCRIPT,
    REFRESH_LOCK_SCRIPT,
    RELEASE_LOCK_SCRIPT,
    SourceStateStore,
)
from .tracking_log_service import TrackingLogAggregationService  # noqa: E402

logger = logging.getLogger(__name__)
SETTINGS = AnalyticsSettings.from_environment()

DB_HOST = SETTINGS.db_host
DB_NAME = SETTINGS.db_name
DB_USER = SETTINGS.db_user
DB_PASSWORD = SETTINGS.db_password
DB_PORT = SETTINGS.db_port
DB_SCHEMA = SETTINGS.db_schema
DATA_SOURCE_LIMIT = SETTINGS.data_source_limit
MAX_WORKERS = SETTINGS.max_workers
SOURCE_BATCH_SIZE = SETTINGS.source_batch_size
OBJECT_BATCH_SIZE = SETTINGS.object_batch_size
REDIS_HOST = SETTINGS.redis_host
REDIS_PORT = SETTINGS.redis_port
REDIS_DB = SETTINGS.redis_db
REDIS_PASSWORD = SETTINGS.redis_password
S3_ENDPOINT_URL = SETTINGS.s3_endpoint_url
S3_REGION = SETTINGS.s3_region
S3_ACCESS_KEY_ID = SETTINGS.s3_access_key_id
S3_SECRET_ACCESS_KEY = SETTINGS.s3_secret_access_key
S3_SESSION_TOKEN = SETTINGS.s3_session_token
S3_FORCE_PATH_STYLE = SETTINGS.s3_force_path_style
S3_VERIFY_SSL = SETTINGS.s3_verify_ssl
S3_MAX_POOL_CONNECTIONS = SETTINGS.s3_max_pool_connections
ANALYTICS_LOCK_KEY = SETTINGS.analytics_lock_key
ANALYTICS_LOCK_TTL_SECONDS = SETTINGS.analytics_lock_ttl_seconds
EVENT_RAW_PREFIX = SETTINGS.event_raw_prefix
SOURCE_LOCK_PREFIX = SETTINGS.source_lock_prefix
SOURCE_STATE_PREFIX = SETTINGS.source_state_prefix
SOURCE_DAILY_PREFIX = SETTINGS.source_daily_prefix
SOURCE_PROFILE_HLL_PREFIX = SETTINGS.source_profile_hll_prefix
LOCK_TTL_SECONDS = SETTINGS.lock_ttl_seconds
PROCESSED_OBJECT_TTL_SECONDS = SETTINGS.processed_object_ttl_seconds
TRACKED_EVENT_FIELD = "tracked-event"
_INCREMENT_IF_NEW_SCRIPT = INCREMENT_IF_NEW_SCRIPT
_RELEASE_LOCK_SCRIPT = RELEASE_LOCK_SCRIPT
_REFRESH_LOCK_SCRIPT = REFRESH_LOCK_SCRIPT


def _load_polars() -> Any:
    import polars as pl

    return pl


def set_tenant_context(cursor: Any, tenant_id: Optional[str]) -> None:
    AnalyticsRepository.set_tenant_context(cursor, tenant_id)


def build_s3_client() -> Any:
    return AnalyticsClientFactory(SETTINGS).build_s3_client()


def build_redis_client() -> Any:
    return AnalyticsClientFactory(SETTINGS).build_redis_client()


def connect_database() -> Any:
    return AnalyticsClientFactory(SETTINGS).connect_database()


def fetch_data_sources(
    connection: Any,
    limit: int = DATA_SOURCE_LIMIT,
) -> list[tuple[str, str]]:
    return AnalyticsRepository(DB_SCHEMA).fetch_data_sources(connection, limit)


def iter_hourly_objects(
    s3_client: Any,
    bucket: str,
    start_after: Optional[str] = None,
    prefix: Optional[str] = None,
) -> Any:
    store = S3EventStore(s3_client, SETTINGS)
    yield from store.iter_hourly_objects(bucket, start_after, prefix)


def _source_daily_key(data_source_id: str) -> str:
    return f"{SOURCE_DAILY_PREFIX}{data_source_id}"


def _source_profile_hll_key(data_source_id: str) -> str:
    return f"{SOURCE_PROFILE_HLL_PREFIX}{data_source_id}"


def current_system_gmt_hour() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")


def s3_json_cache_key(bucket: str, object_key: str) -> str:
    return f"s3://{bucket}/{object_key}"


def _state_store(redis_client: Any) -> SourceStateStore:
    return SourceStateStore(redis_client, SETTINGS, current_system_gmt_hour)


def _get_source_state_int(
    redis_client: Any,
    data_source_id: str,
    field: str,
) -> Optional[int]:
    return _state_store(redis_client).get_state_int(data_source_id, field)


def _increment_source_cached_total(
    redis_client: Any,
    data_source_id: str,
    increment: int,
) -> None:
    _state_store(redis_client).increment_cached_total(data_source_id, increment)


def _increment_source_daily_total(
    redis_client: Any,
    data_source_id: str,
    day: str,
    increment: int,
) -> None:
    _state_store(redis_client).increment_daily_total(data_source_id, day, increment)


def _add_source_profile_signatures(
    redis_client: Any,
    data_source_id: str,
    signatures: set[str],
) -> None:
    _state_store(redis_client).add_profile_signatures(data_source_id, signatures)


def _get_daily_stats(redis_client: Any, data_source_id: str) -> tuple[int, int]:
    return _state_store(redis_client).get_daily_stats(data_source_id)


def _aggregate_source_results(results: list[dict[str, Any]]) -> dict[str, int]:
    return AnalyticsMetrics.aggregate_source_results(results)


_parse_event_datetime = EventRecordService.parse_event_datetime
_text_value = EventRecordService.text_value
_identity_value = EventRecordService.identity_value
_iter_jsonl_lines = EventRecordService.iter_jsonl_lines
_extract_profile_signature = EventRecordService.extract_profile_signature


def count_jsonl_records(body: Any, object_key: str) -> int:
    return EventRecordService(DB_SCHEMA).count_jsonl_records(body, object_key)


def summarize_bucket_metrics(s3_client: Any, bucket: str) -> tuple[int, int, float]:
    return EventRecordService(DB_SCHEMA).summarize_bucket_metrics(
        s3_client,
        bucket,
        iter_hourly_objects,
    )


def count_records_and_signatures(
    body: Any,
    object_key: str,
) -> tuple[int, set[str]]:
    return EventRecordService(DB_SCHEMA).count_records_and_signatures(body, object_key)


def normalize_event_record(
    record: dict[str, Any],
    data_source_id: str,
    tenant_id: str,
) -> dict[str, Any]:
    return EventRecordService(DB_SCHEMA).normalize_event_record(
        record,
        data_source_id,
        tenant_id,
    )


def read_normalized_event_records(
    body: Any,
    object_key: str,
    data_source_id: str,
    tenant_id: str,
) -> list[dict[str, Any]]:
    return EventRecordService(DB_SCHEMA).read_normalized_event_records(
        body,
        object_key,
        data_source_id,
        tenant_id,
    )


def upsert_raw_profile(cursor: Any, event: dict[str, Any]) -> str:
    return EventRecordService(DB_SCHEMA).upsert_raw_profile(cursor, event)


def _source_lock_key(data_source_id: str) -> str:
    return f"{SOURCE_LOCK_PREFIX}{data_source_id}"


def _source_state_key(data_source_id: str) -> str:
    return f"{SOURCE_STATE_PREFIX}{data_source_id}"


def _set_source_state(redis_client: Any, data_source_id: str, **values: Any) -> None:
    _state_store(redis_client).set_state(data_source_id, **values)


def acquire_source_lock(
    redis_client: Any,
    data_source_id: str,
    run_id: str,
) -> Optional[str]:
    return _state_store(redis_client).acquire_source_lock(data_source_id, run_id)


def refresh_source_lock(redis_client: Any, data_source_id: str, token: str) -> None:
    _state_store(redis_client).refresh_source_lock(data_source_id, token)


def release_source_lock(redis_client: Any, data_source_id: str, token: str) -> None:
    _state_store(redis_client).release_source_lock(data_source_id, token)


def get_source_cursor(redis_client: Any, data_source_id: str) -> Optional[str]:
    return _state_store(redis_client).get_source_cursor(data_source_id)


def save_source_cursor(
    redis_client: Any,
    data_source_id: str,
    hour: str,
    object_key: str,
) -> None:
    _state_store(redis_client).save_source_cursor(data_source_id, hour, object_key)


def get_source_statuses(redis_client: Any) -> list[dict[str, str]]:
    return _state_store(redis_client).get_source_statuses()


def increment_hourly_count(
    redis_client: Any,
    data_source_id: str,
    hour: str,
    bucket: str,
    object_key: str,
    event_count: int,
) -> bool:
    return _state_store(redis_client).increment_hourly_count(
        data_source_id,
        hour,
        bucket,
        object_key,
        event_count,
    )


def update_data_source_summary(
    connection: Any,
    tenant_id: str,
    data_source_id: str,
    total_tracked_event: int,
    avg_daily_event: int,
    avg_events_per_profile: float,
) -> None:
    AnalyticsRepository(DB_SCHEMA).update_data_source_summary(
        connection,
        tenant_id,
        data_source_id,
        total_tracked_event,
        avg_daily_event,
        avg_events_per_profile,
    )


def process_tracking_logs(
    *,
    s3_client: Optional[Any] = None,
    redis_client: Optional[Any] = None,
    db_connection: Optional[Any] = None,
    data_source_limit: int = DATA_SOURCE_LIMIT,
    run_id: Optional[str] = None,
    log: Optional[Callable[..., None]] = None,
    _lock_acquired: bool = False,
    _global_lease: Optional[Any] = None,
) -> dict[str, int]:
    """Process hourly JSONL logs through the injected analytics service."""
    runtime_settings = replace(
        SETTINGS,
        max_workers=MAX_WORKERS,
        source_batch_size=SOURCE_BATCH_SIZE,
        object_batch_size=OBJECT_BATCH_SIZE,
    )
    client_factory = AnalyticsClientFactory(runtime_settings)
    storage = s3_client if s3_client is not None else client_factory.build_s3_client()
    cache = redis_client if redis_client is not None else client_factory.build_redis_client()
    service = TrackingLogAggregationService(
        settings=runtime_settings,
        s3_client=storage,
        redis_client=cache,
        db_connection=db_connection,
        source_loader=fetch_data_sources,
        database_connector=client_factory.connect_database,
        run_id=run_id,
        log=log or logger.info,
        clock=current_system_gmt_hour,
    )
    return service.run(
        data_source_limit=data_source_limit,
        lock_acquired=_lock_acquired,
        global_lease=_global_lease,
    )