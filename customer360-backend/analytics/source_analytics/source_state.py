"""Redis state, leases, checkpoints, and counters for source analytics."""

from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import uuid4

from .config import AnalyticsSettings
from .metrics import AnalyticsMetrics


INCREMENT_IF_NEW_SCRIPT = """
if redis.call('SET', KEYS[2], ARGV[1], 'NX', 'EX', ARGV[4]) then
    redis.call('HINCRBY', KEYS[1], ARGV[2], ARGV[3])
    return 1
end
return 0
"""
RELEASE_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""
REFRESH_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""


class SourceStateStore:
    """Own Redis state transitions for one analytics run."""

    def __init__(
        self,
        redis_client: Any,
        settings: AnalyticsSettings,
        clock: Callable[[], str],
    ) -> None:
        self.redis_client = redis_client
        self.settings = settings
        self.clock = clock

    def source_lock_key(self, data_source_id: str) -> str:
        return f"{self.settings.source_lock_prefix}{data_source_id}"

    def source_state_key(self, data_source_id: str) -> str:
        return f"{self.settings.source_state_prefix}{data_source_id}"

    def source_daily_key(self, data_source_id: str) -> str:
        return f"{self.settings.source_daily_prefix}{data_source_id}"

    def source_profile_hll_key(self, data_source_id: str) -> str:
        return f"{self.settings.source_profile_hll_prefix}{data_source_id}"

    def source_profile_analytics_key(
        self, data_source_id: str, raw_profile_id: str
    ) -> str:
        return f"{self.settings.source_profile_analytics_prefix}{data_source_id}:{raw_profile_id}"

    def source_profile_event_key(self, data_source_id: str, event_id: str) -> str:
        return f"{self.settings.source_profile_event_prefix}{data_source_id}:{event_id}"

    def set_state(self, data_source_id: str, **values: Any) -> None:
        values["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.redis_client.hset(
            self.source_state_key(data_source_id),
            mapping={
                key: str(value) for key, value in values.items() if value is not None
            },
        )

    def get_state_int(self, data_source_id: str, field: str) -> Optional[int]:
        raw = self.redis_client.hgetall(self.source_state_key(data_source_id)).get(field)
        if raw is None or str(raw).strip() == "":
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def increment_cached_total(self, data_source_id: str, increment: int) -> None:
        if increment <= 0:
            return
        state_key = self.source_state_key(data_source_id)
        state = self.redis_client.hgetall(state_key)
        current = int(state.get("total_tracked_event_cache", "0") or "0")
        self.redis_client.hset(
            state_key,
            mapping={"total_tracked_event_cache": str(current + increment)},
        )

    def increment_daily_total(
        self,
        data_source_id: str,
        day: str,
        increment: int,
    ) -> None:
        if increment <= 0:
            return
        daily_key = self.source_daily_key(data_source_id)
        daily = self.redis_client.hgetall(daily_key)
        current = int(daily.get(day, "0") or "0")
        self.redis_client.hset(daily_key, mapping={day: str(current + increment)})

    def add_profile_signatures(
        self,
        data_source_id: str,
        signatures: set[str],
    ) -> None:
        if signatures:
            self.redis_client.pfadd(
                self.source_profile_hll_key(data_source_id), *sorted(signatures)
            )

    def profile_count(self, data_source_id: str) -> int:
        return int(self.redis_client.pfcount(self.source_profile_hll_key(data_source_id)))

    def record_profile_event_analytics(
        self,
        data_source_id: str,
        raw_profile_id: str,
        event_id: str,
        *,
        page_view: bool,
        click: bool,
    ) -> dict[str, float | int]:
        """Accumulate one deduplicated event and return its profile snapshot."""
        accepted = self.redis_client.set(
            self.source_profile_event_key(data_source_id, event_id),
            "1",
            nx=True,
            ex=self.settings.processed_object_ttl_seconds,
        )
        profile_key = self.source_profile_analytics_key(data_source_id, raw_profile_id)
        if accepted:
            increments = {"total_tracked_events": 1}
            if page_view:
                increments["page_views"] = 1
            if click:
                increments["clicks"] = 1
            for field, increment in increments.items():
                self.redis_client.hincrby(profile_key, field, increment)

        values = self.redis_client.hgetall(profile_key)
        total = int(values.get("total_tracked_events", 0) or 0)
        page_views = int(values.get("page_views", 0) or 0)
        clicks = int(values.get("clicks", 0) or 0)
        return {
            "page_views": page_views,
            "clicks": clicks,
            "total_tracked_events": total,
            "click_through_rate": round(clicks / page_views, 6) if page_views else 0.0,
        }

    def get_daily_stats(self, data_source_id: str) -> tuple[int, int]:
        daily = self.redis_client.hgetall(self.source_daily_key(data_source_id))
        return AnalyticsMetrics.daily_stats(daily)

    def acquire_source_lock(self, data_source_id: str, run_id: str) -> Optional[str]:
        token = str(uuid4())
        acquired = self.redis_client.set(
            self.source_lock_key(data_source_id),
            token,
            nx=True,
            ex=self.settings.lock_ttl_seconds,
        )
        if not acquired:
            return None
        self.set_state(
            data_source_id,
            status="running",
            run_id=run_id,
            started_at=datetime.now(timezone.utc).isoformat(),
            last_error="",
        )
        return token

    def refresh_source_lock(self, data_source_id: str, token: str) -> None:
        refreshed = self.redis_client.eval(
            REFRESH_LOCK_SCRIPT,
            1,
            self.source_lock_key(data_source_id),
            token,
            str(self.settings.lock_ttl_seconds),
        )
        if int(refreshed) != 1:
            raise RuntimeError(f"Analytics lock was lost for data source {data_source_id}")

    def release_source_lock(self, data_source_id: str, token: str) -> None:
        self.redis_client.eval(
            RELEASE_LOCK_SCRIPT,
            1,
            self.source_lock_key(data_source_id),
            token,
        )

    def get_source_cursor(self, data_source_id: str) -> Optional[str]:
        state = self.redis_client.hgetall(self.source_state_key(data_source_id))
        return state.get("last_processed_object") or None

    def get_source_last_hour(self, data_source_id: str) -> Optional[str]:
        state = self.redis_client.hgetall(self.source_state_key(data_source_id))
        return state.get("last_processed_hour") or None

    def save_source_cursor(
        self,
        data_source_id: str,
        hour: str,
        object_key: str,
    ) -> None:
        self.set_state(
            data_source_id,
            last_processed_hour=hour,
            last_processed_object=object_key,
        )

    def increment_hourly_count(
        self,
        data_source_id: str,
        hour: str,
        bucket: str,
        object_key: str,
        event_count: int,
    ) -> bool:
        if event_count < 0:
            raise ValueError("event count cannot be negative")
        hourly_key = f"{data_source_id}-{hour}"
        checkpoint_key = f"s3://{bucket}/{object_key}"
        result = self.redis_client.eval(
            INCREMENT_IF_NEW_SCRIPT,
            2,
            hourly_key,
            checkpoint_key,
            self.clock(),
            "tracked-event",
            str(event_count),
            str(self.settings.processed_object_ttl_seconds),
        )
        return int(result) == 1

    def get_source_statuses(self) -> list[dict[str, str]]:
        statuses: list[dict[str, str]] = []
        for state_key in self.redis_client.scan_iter(
            match=f"{self.settings.source_state_prefix}*"
        ):
            data_source_id = str(state_key)[len(self.settings.source_state_prefix) :]
            state = {
                str(key): str(value)
                for key, value in self.redis_client.hgetall(state_key).items()
            }
            if self.redis_client.exists(self.source_lock_key(data_source_id)):
                state["status"] = "running"
            elif state.get("status") == "running":
                state["status"] = "stale"
            state["data_source_id"] = data_source_id
            statuses.append(state)
        return sorted(statuses, key=lambda status: status["data_source_id"])