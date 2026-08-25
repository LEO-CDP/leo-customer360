"""Dagster job/sensor for the web-tracking event Loader (backend-system/event_loader).

Wraps ``event_loader.loader.run_loader_once`` (Redis Streams -> Postgres) in a Dagster
op/job so every drain cycle is tracked, retried, and observable, and a sensor requests a
new run every poll interval (the unified backend-system image runs the Dagster daemon, so
the sensor replaces a bare ``while True`` consumer loop). The Loader consumes the broker
Redis stream produced by ``data-tracking-api`` on the tracking box; see
``deployments/docs/web-tracking-implementation-plan.md`` §6-8 and ``event_loader/loader.py``.

Load alongside the other locations from ``backend-system/``:
``dagster dev -w workspace.yaml``.
"""

import os
import sys

# `python_file` workspace loading does NOT add this file's dir to sys.path, so make the
# nested `event_loader` package importable regardless of how this module is loaded
# (same guard as identity_resolution/dagster_defs.py).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dagster import (  # noqa: E402
    DefaultSensorStatus,
    Definitions,
    OpExecutionContext,
    RetryPolicy,
    RunRequest,
    SensorEvaluationContext,
    job,
    op,
    sensor,
)

from event_loader.loader import run_loader_once  # noqa: E402

POLL_INTERVAL_SECONDS = int(os.environ.get("EVENT_LOADER_POLL_INTERVAL_SECONDS", "15"))


@op(retry_policy=RetryPolicy(max_retries=3, delay=10))
def drain_event_stream_op(context: OpExecutionContext) -> int:
    """Drain one batch from the broker stream into cdp_raw_events (see loader.run_loader_once)."""
    consumed = run_loader_once()
    context.log.info("event_loader: drained %d event(s) this cycle", consumed)
    return consumed


@job(name="event_loader_job")
def event_loader_job() -> None:
    drain_event_stream_op()


@sensor(
    job=event_loader_job,
    minimum_interval_seconds=POLL_INTERVAL_SECONDS,
    default_status=DefaultSensorStatus.RUNNING,
)
def event_loader_poll_sensor(context: SensorEvaluationContext):
    """Request an event_loader_job run every poll interval (Dagster-daemon driven)."""
    yield RunRequest()


defs = Definitions(jobs=[event_loader_job], sensors=[event_loader_poll_sensor])
