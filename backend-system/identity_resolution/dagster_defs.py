"""Dagster job/sensor definitions for the Customer Identity Resolution (CIR)
service.

Wraps the existing ``identity_resolution.daily_job.run_daily_identity_resolution``
batch-drain logic in a Dagster op/job so every run is tracked, retried, and
observable (Dagit/webserver run history, per-step logs, alerts) instead of a
bare ``while True`` polling loop:

  - ``resolve_identities_op`` / ``identity_resolution_job`` -- one CIR
    resolution cycle (drains ``cdp_raw_profiles_stage`` until empty).
  - ``identity_resolution_poll_sensor`` -- requests a new run every
    ``CIR_POLL_INTERVAL_SECONDS``, replicating the old worker.py polling
    loop, but launched/tracked by the Dagster daemon once one is running.
    Stopped by default (opt-in), since ``worker.py`` already drives this job
    in-process for the current single-process container deployment.

See ``worker.py`` for the container entrypoint that executes this job
in-process on a timer, and ``../workspace.yaml`` (in ``backend-system/``) to
load this code location alongside ``scoring``/``segmentation``/``analytics``
in the Dagster UI: ``dagster dev -w ../workspace.yaml`` (run from this
directory, or adjust the path). Set ``DAGSTER_HOME`` to a persistent
directory to keep run history across restarts; without it, Dagster uses an
ephemeral in-memory instance.
"""

import os
import sys

# Dagster's `python_file` workspace loader (unlike `python dagster_defs.py`
# directly, or pytest with `pythonpath = .`) does NOT add this file's own
# directory to sys.path before importing it -- so without this, `import
# identity_resolution` (the nested package next to this file) fails when
# loaded via `dagster dev -w ../workspace.yaml`. Make it robust regardless
# of how this module is loaded.
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

from identity_resolution.daily_job import run_daily_identity_resolution  # noqa: E402

POLL_INTERVAL_SECONDS = int(os.environ.get("CIR_POLL_INTERVAL_SECONDS", "30"))


@op(retry_policy=RetryPolicy(max_retries=2, delay=10))
def resolve_identities_op(context: OpExecutionContext) -> int:
    """Drains cdp_raw_profiles_stage in batches via CustomerIdentityResolver."""
    context.log.info("CIR identity resolution job: started")
    processed = run_daily_identity_resolution()
    context.log.info(f"CIR identity resolution job: done (processed={processed})")
    return processed


@job(name="identity_resolution_job")
def identity_resolution_job() -> None:
    resolve_identities_op()


@sensor(
    job=identity_resolution_job,
    minimum_interval_seconds=POLL_INTERVAL_SECONDS,
    default_status=DefaultSensorStatus.STOPPED,
)
def identity_resolution_poll_sensor(context: SensorEvaluationContext):
    """Requests a new identity_resolution_job run every poll interval.

    Enable this (from the Dagster UI, or `default_status=RUNNING`) once a
    real Dagster daemon is deployed for this service, instead of relying on
    worker.py's in-process loop, to get daemon-managed scheduling/scaling
    (e.g. run concurrency limits, backfills) on top of the per-run
    monitoring this code location already provides.
    """
    yield RunRequest()


defs = Definitions(
    jobs=[identity_resolution_job],
    sensors=[identity_resolution_poll_sensor],
)
