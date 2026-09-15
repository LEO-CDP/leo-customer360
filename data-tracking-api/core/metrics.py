"""Process-local Prometheus metrics for tracking ingestion."""

from threading import Lock
from typing import Any

from core.config import Settings


class TrackingMetrics:
    """Maintain low-cardinality counters for one API process."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[str, int] = {}

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counters)


tracking_metrics = TrackingMetrics()


def render_prometheus_metrics(
    settings: Settings,
    tracking_storage: Any,
) -> str:
    """Render queue gauges, configured limits, and ingestion counters."""
    try:
        queue_depth = int(tracking_storage.pending_count())
    except Exception:
        queue_depth = 0
    try:
        oldest_pending_age = max(
            0.0, float(tracking_storage.oldest_pending_age_seconds())
        )
    except Exception:
        oldest_pending_age = 0.0
    try:
        queue_capacity = int(tracking_storage.queue_capacity())
    except Exception:
        queue_capacity = 0

    values: dict[str, float | int] = {
        "tracking_queue_depth": queue_depth,
        "tracking_queue_capacity": queue_capacity,
        "tracking_queue_oldest_pending_age_seconds": oldest_pending_age,
        "tracking_request_max_body_bytes": settings.max_request_body_bytes,
        "tracking_request_max_events": settings.max_events_per_request,
        "tracking_batch_flush_size": settings.tracking_log_flush_batch_size,
        "tracking_object_max_bytes": settings.event_max_object_size_bytes,
        **tracking_metrics.snapshot(),
    }
    metric_help = {
        "tracking_queue_depth": "Current number of tracking batches in the handoff queue.",
        "tracking_queue_capacity": "Configured maximum number of tracking batches in the handoff queue.",
        "tracking_queue_oldest_pending_age_seconds": "Age of the oldest pending tracking batch.",
        "tracking_request_max_body_bytes": "Maximum accepted tracking request body size in bytes.",
        "tracking_request_max_events": "Maximum accepted events in one tracking request.",
        "tracking_batch_flush_size": "Maximum batches read by one queue flush operation.",
        "tracking_object_max_bytes": "Maximum compressed Bronze object size in bytes.",
    }
    lines = ["# HELP tracking_service_info Tracking service metadata.", "# TYPE tracking_service_info gauge", "tracking_service_info{version=\"1\"} 1"]
    for name, value in values.items():
        if name in metric_help:
            metric_type = "gauge"
            lines.extend([f"# HELP {name} {metric_help[name]}", f"# TYPE {name} {metric_type}"])
        elif name.endswith("_total"):
            lines.extend([f"# TYPE {name} counter"])
        else:
            lines.extend([f"# TYPE {name} gauge"])
        lines.append(f"{name} {value}")
    return "\n".join(lines) + "\n"