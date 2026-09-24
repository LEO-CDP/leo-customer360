"""Redis lease/state and Dagster operations for manual analytics runs."""

from typing import Any

from redis.exceptions import RedisError


class AnalyticsRepository:
    """Encapsulates analytics status persistence and job submission."""

    def __init__(self, redis_client, dagster_client):
        """Create an analytics repository with injected service clients."""
        self.redis = redis_client
        self.dagster = dagster_client

    def status(self, source_state_prefix: str, source_lock_prefix: str, submission_state_key: str, submission_lock_key: str) -> dict[str, Any]:
        """Read source processing state and the global submission lease."""
        if self.redis is None:
            raise RuntimeError("Analytics status is unavailable without Redis")
        statuses = []
        for state_key in self.redis.scan_iter(match=f"{source_state_prefix}*"):
            data_source_id = str(state_key)[len(source_state_prefix):]
            state = {str(key): str(value) for key, value in self.redis.hgetall(state_key).items()}
            if self.redis.exists(f"{source_lock_prefix}{data_source_id}"):
                state["status"] = "running"
            elif state.get("status") == "running":
                state["status"] = "stale"
            state["data_source_id"] = data_source_id
            statuses.append(state)
        statuses.sort(key=lambda item: item["data_source_id"])
        submission_state = {str(key): str(value) for key, value in self.redis.hgetall(submission_state_key).items()}
        submission_active = bool(self.redis.exists(submission_lock_key))
        if submission_active and submission_state.get("run_id"):
            submission_state["status"] = "submitted"
        running_ids = [item["data_source_id"] for item in statuses if item.get("status") == "running"]
        return {"status": "running" if running_ids or submission_active else "idle", "can_trigger": not running_ids and not submission_active, "running_data_source_ids": running_ids, "data_sources": statuses, "active_submission": submission_state if submission_active else None}

    def reserve_submission(self, key: str, ttl: int) -> bool:
        """Acquire the single analytics submission lease."""
        return bool(self.redis.set(key, "reserved", nx=True, ex=ttl))

    def release_submission(self, key: str) -> None:
        """Release the analytics submission lease."""
        self.redis.delete(key)

    def process(self):
        """Submit the Dagster tracking-log aggregation job."""
        return self.dagster.analytics.process_tracking_logs()

    def get_status(self, run_id: str):
        """Read the Dagster status for a submitted analytics run."""
        return self.dagster.analytics.get_status(run_id)

    def record_submission(self, key: str, run_id: str) -> None:
        """Persist the submitted run identifier and trigger reason."""
        self.redis.hset(key, mapping={"run_id": run_id, "status": "submitted", "trigger_reason": "manual_api"})