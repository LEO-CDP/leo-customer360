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
        loop, launched and tracked by the Dagster daemon.

See ``../workspace.yaml`` (in ``backend-system/``) to
load this code location alongside ``scoring``/``segmentation``/``analytics``
in the Dagster UI: ``dagster dev -w ../workspace.yaml`` (run from this
directory, or adjust the path). Set ``DAGSTER_HOME`` to a persistent
directory to keep run history across restarts; without it, Dagster uses an
ephemeral in-memory instance.
"""

import os
import sys
from typing import Optional

# Dagster's `python_file` workspace loader (unlike `python dagster_defs.py`
# directly, or pytest with `pythonpath = .`) does NOT add this file's own
# directory to sys.path before importing it -- so without this, `import
# identity_resolution` (the nested package next to this file) fails when
# loaded via `dagster dev -w ../workspace.yaml`. Make it robust regardless
# of how this module is loaded.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dagster import (  # noqa: E402
        Config,
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

from identity_resolution.daily_job import (  # noqa: E402
    recompute_persona_archetype_match_count,
    run_daily_identity_resolution,
)

POLL_INTERVAL_SECONDS = int(os.environ.get("CIR_POLL_INTERVAL_SECONDS", "30"))


class IdentityResolutionConfig(Config):
    """Optional config for a full CIR run or a targeted persona refresh."""

    tenant_id: Optional[str] = None
    persona_archetype_id: Optional[str] = None


@op(retry_policy=RetryPolicy(max_retries=2, delay=10))
def resolve_identities_op(context: OpExecutionContext, config: IdentityResolutionConfig) -> int:
    """Runs full CIR or refreshes one persona archetype's match count."""
    if config.persona_archetype_id:
        if not config.tenant_id:
            raise ValueError("tenant_id is required for a targeted persona archetype refresh")
        matched_profile_count = recompute_persona_archetype_match_count(
            tenant_id=config.tenant_id,
            persona_archetype_id=config.persona_archetype_id,
        )
        context.log.info(
            "Persona archetype recompute: persona_archetype_id=%s tenant_id=%s matched_profile_count=%d",
            config.persona_archetype_id,
            config.tenant_id,
            matched_profile_count,
        )
        return matched_profile_count

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
    default_status=DefaultSensorStatus.RUNNING,
)
def identity_resolution_poll_sensor(context: SensorEvaluationContext):
    """Requests a new identity_resolution_job run every poll interval.

    The unified backend-system image runs the Dagster daemon, so this sensor is
    enabled by default and replaces the legacy worker.py polling loop.
    """
    yield RunRequest()


defs = Definitions(
    jobs=[identity_resolution_job],
    sensors=[identity_resolution_poll_sensor],
)
