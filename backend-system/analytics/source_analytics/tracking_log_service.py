"""Object-oriented orchestration for tracking-log analytics."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import uuid4

from .config import AnalyticsSettings
from .event_records import EventRecordService
from .metrics import AnalyticsMetrics
from .object_store import S3EventStore
from .repositories import AnalyticsRepository
from .source_state import SourceStateStore


class TrackingLogAggregationService:
    """Coordinate source discovery, event processing, and summary writes."""

    def __init__(
        self,
        *,
        settings: AnalyticsSettings,
        s3_client: Any,
        redis_client: Any,
        db_connection: Optional[Any],
        source_loader: Callable[[Any, int], list[tuple[str, str]]],
        database_connector: Callable[[], Any],
        run_id: Optional[str] = None,
        log: Optional[Callable[..., None]] = None,
        clock: Optional[Callable[[], str]] = None,
    ) -> None:
        self.settings = settings
        self.storage = S3EventStore(s3_client, settings)
        self.state = SourceStateStore(
            redis_client,
            settings,
            clock or self._current_system_gmt_hour,
        )
        self.database = AnalyticsRepository(settings.db_schema)
        self.events = EventRecordService(settings.db_schema)
        self.db_connection = db_connection
        self.source_loader = source_loader
        self.database_connector = database_connector
        self.run_id = run_id or str(uuid4())
        self.log = log or (lambda *_args, **_kwargs: None)

    @staticmethod
    def _current_system_gmt_hour() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")

    def run(
        self,
        *,
        data_source_limit: int,
        lock_acquired: bool = False,
        global_lease: Optional[Any] = None,
    ) -> dict[str, int]:
        """Run one globally serialized analytics batch."""
        if lock_acquired:
            return self._run_locked(data_source_limit, global_lease)

        from shared.redis_lock import acquire_redis_lease

        lease = acquire_redis_lease(
            self.state.redis_client,
            self.settings.analytics_lock_key,
            self.settings.analytics_lock_ttl_seconds,
        )
        if lease is None:
            self.log("Skipping analytics run because another run owns the global lock")
            return AnalyticsMetrics.empty_summary()
        try:
            return self._run_locked(data_source_limit, lease)
        finally:
            lease.release()

    def _run_locked(
        self,
        data_source_limit: int,
        global_lease: Optional[Any],
    ) -> dict[str, int]:
        if self.db_connection is not None:
            source_items = self.source_loader(self.db_connection, data_source_limit)
            source_results = [
                self._process_source(data_source_id, tenant_id, global_lease)
                for data_source_id, tenant_id in source_items
            ]
            return AnalyticsMetrics.aggregate_source_results(source_results)

        seed_connection = self.database_connector()
        try:
            source_items = self.source_loader(seed_connection, data_source_limit)
        finally:
            seed_connection.close()

        source_results: list[dict[str, Any]] = []
        for source_batch_start in range(0, len(source_items), self.settings.source_batch_size):
            source_batch = source_items[
                source_batch_start : source_batch_start + self.settings.source_batch_size
            ]
            worker_count = max(1, min(self.settings.max_workers, len(source_batch)))
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(
                        self._process_source,
                        data_source_id,
                        tenant_id,
                        global_lease,
                    ): (data_source_id, tenant_id)
                    for data_source_id, tenant_id in source_batch
                }
                for future in as_completed(futures):
                    source_results.append(future.result())
            if global_lease is not None:
                global_lease.refresh()
        return AnalyticsMetrics.aggregate_source_results(source_results)

    def _process_source(
        self,
        data_source_id: str,
        tenant_id: str,
        global_lease: Optional[Any],
    ) -> dict[str, Any]:
        lock_token = self.state.acquire_source_lock(data_source_id, self.run_id)
        if lock_token is None:
            self.log(
                "Skipping data source %s because another analytics run owns its lock",
                data_source_id,
            )
            return {
                "data_source_id": data_source_id,
                "tenant_id": tenant_id,
                "skipped_running": True,
                "objects_processed": 0,
                "events_added": 0,
            }

        source_connection = self.db_connection
        owns_source_connection = False
        source_increment = 0
        source_objects_processed = 0
        saw_checkpointed_object = False
        bucket = f"data-tracking-{data_source_id}"
        try:
            if source_connection is None:
                source_connection = self.database_connector()
                owns_source_connection = True
            with source_connection.cursor() as context_cursor:
                self.database.set_tenant_context(context_cursor, tenant_id)

            start_after = self._source_start_after(data_source_id)
            prefix = f"{self.settings.event_raw_prefix}/"

            for hour, object_key in self.storage.iter_hourly_objects(
                bucket,
                start_after=start_after,
                prefix=prefix,
            ):
                if source_objects_processed >= self.settings.object_batch_size:
                    self.log(
                        "Pausing source %s after %d objects; next run resumes from the saved cursor",
                        data_source_id,
                        self.settings.object_batch_size,
                    )
                    break
                if global_lease is not None:
                    global_lease.refresh()
                self.state.refresh_source_lock(data_source_id, lock_token)
                event_count, signatures = self._process_object(
                    source_connection,
                    bucket,
                    object_key,
                    data_source_id,
                    tenant_id,
                )
                if self.state.increment_hourly_count(
                    data_source_id,
                    hour,
                    bucket,
                    object_key,
                    event_count,
                ):
                    source_objects_processed += 1
                    source_increment += event_count
                    self.state.increment_cached_total(data_source_id, event_count)
                    self.state.increment_daily_total(
                        data_source_id, hour[:10], event_count
                    )
                    self.state.add_profile_signatures(data_source_id, signatures)
                    self.log(
                        "Processed %s records from %s/%s",
                        event_count,
                        bucket,
                        object_key,
                    )
                else:
                    saw_checkpointed_object = True
                self.state.save_source_cursor(data_source_id, hour, object_key)

            total_tracked_event = self.state.get_state_int(
                data_source_id,
                "total_tracked_event_cache",
            )
            if total_tracked_event is None and saw_checkpointed_object:
                total_tracked_event, _, _ = self.events.summarize_bucket_metrics(
                    self.storage.client,
                    bucket,
                    self._iter_objects_for_metrics,
                )
                self.state.redis_client.hset(
                    self.state.source_state_key(data_source_id),
                    mapping={"total_tracked_event_cache": str(total_tracked_event)},
                )
            elif total_tracked_event is None:
                total_tracked_event = 0

            active_days, daily_total = self.state.get_daily_stats(data_source_id)
            profile_count = self.state.profile_count(data_source_id)
            avg_daily_event = round(daily_total / active_days) if active_days > 0 else 0
            avg_events_per_profile = (
                round(total_tracked_event / profile_count, 2)
                if profile_count > 0
                else 0.0
            )
            self.database.update_data_source_summary(
                source_connection,
                tenant_id,
                data_source_id,
                total_tracked_event,
                avg_daily_event,
                avg_events_per_profile,
            )
            self.state.set_state(
                data_source_id,
                status="completed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                objects_processed=source_objects_processed,
                events_added=source_increment,
                last_error="",
            )
            return {
                "data_source_id": data_source_id,
                "tenant_id": tenant_id,
                "skipped_running": False,
                "objects_processed": source_objects_processed,
                "events_added": source_increment,
            }
        except Exception as exc:
            self.state.set_state(
                data_source_id,
                status="failed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                last_error=str(exc),
            )
            raise
        finally:
            self.state.release_source_lock(data_source_id, lock_token)
            if owns_source_connection and source_connection is not None:
                source_connection.close()

    def _process_object(
        self,
        source_connection: Any,
        bucket: str,
        object_key: str,
        data_source_id: str,
        tenant_id: str,
    ) -> tuple[int, set[str]]:
        response = self.storage.get_object(bucket, object_key)
        body = response["Body"]
        event_count = 0
        signatures: set[str] = set()
        try:
            with source_connection.cursor() as profile_cursor:
                for normalized_event in self.events.iter_normalized_event_records(
                    body,
                    object_key,
                    data_source_id,
                    tenant_id,
                ):
                    event_count += 1
                    signature = self.events.extract_profile_signature(
                        {"payload": normalized_event["payload"]}
                    )
                    if signature:
                        signatures.add(signature)
                    self.events.upsert_raw_profile(profile_cursor, normalized_event)
        finally:
            close = getattr(body, "close", None)
            if close:
                close()
        return event_count, signatures

    def _source_start_after(self, data_source_id: str) -> Optional[str]:
        start_after = self.state.get_source_cursor(data_source_id)
        last_processed_hour = self.state.get_source_last_hour(data_source_id)
        prefix = f"{self.settings.event_raw_prefix}/"
        # API batches are UUID-keyed within the active receive-hour prefix.
        # A lexicographic StartAfter cursor can therefore hide a later object
        # whose UUID sorts before the previously saved object.
        if last_processed_hour == self.state.clock():
            return None
        if not start_after or not start_after.startswith(prefix):
            return None
        return start_after

    def _iter_objects_for_metrics(
        self,
        _s3_client: Any,
        bucket: str,
    ) -> Any:
        return self.storage.iter_hourly_objects(bucket)