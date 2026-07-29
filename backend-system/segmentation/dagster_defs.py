"""Dagster job/sensor definitions for the Segmentation service
(backend-system/segmentation).

Recomputes ``cdp_segments`` membership (``member_count`` +
``cdp_master_profiles.segmentation_tags`` sync) for every ``is_active``
segment, either across all tenants (scheduled) or scoped to one tenant
(on-demand) -- the scheduled/batch counterpart to customer360-api's
on-demand ``POST /api/v1/segments/{id}/recompute``
(``customer360-api/core/crud/segmentation.py``). See
``docs/PLAN-SEGMENTS-API-IMPROVEMENT.md`` Phase 3.

  - ``recompute_segments_op`` / ``segmentation_job`` -- one full pass over
    every active segment (see ``segmentation/recompute.py``), scoped to
    ``RecomputeSegmentsConfig.tenant_id`` if the run was submitted with one
    (e.g. customer360-api's admin "Refresh" button -- see
    ``core/utils/dagster_client.py``), otherwise ALL tenants.
  - ``segmentation_poll_sensor`` -- polls ``cdp_master_profiles`` every
    ``SEGMENTATION_POLL_INTERVAL_SECONDS`` (default 10s) and only requests a
    new ``segmentation_job`` run when at least one profile was created or
    updated since the last check. This intentionally is NOT a real-time,
    per-row trigger -- it's a fixed-interval poll (same pattern as
    ``identity_resolution_poll_sensor`` in
    ``../identity_resolution/dagster_defs.py``), gated on an actual
    "something changed since last time" check via a Dagster sensor cursor,
    instead of firing unconditionally every tick. Membership can therefore
    lag a real create/update by up to one poll interval. RUNNING by default
    (unlike identity_resolution's sensor) since no worker.py loop drives this
    job today. Requests runs with no config, so each scheduled pass covers
    ALL tenants.

Run from `backend-system/`: `dagster dev -w workspace.yaml` to see this job
(alongside `identity_resolution`/`scoring`/`analytics`) in the Dagster UI.
"""

import os
import sys
from datetime import datetime, timezone
from typing import Optional

# Dagster's `python_file` workspace loader does NOT add this file's own
# directory to sys.path the way `python dagster_defs.py` directly would --
# so without this, `import segmentation` (the nested package next to this
# file) fails when loaded via `dagster dev -w workspace.yaml`. Same gotcha
# documented in ../identity_resolution/dagster_defs.py.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dagster import (  # noqa: E402
    Config,
    DefaultSensorStatus,
    Definitions,
    OpExecutionContext,
    RetryPolicy,
    RunRequest,
    SensorEvaluationContext,
    SkipReason,
    job,
    op,
    sensor,
)

from segmentation.recompute import (  # noqa: E402
    count_recently_changed_master_profiles,
    recompute_all_active_segments,
)

POLL_INTERVAL_SECONDS = int(os.environ.get("SEGMENTATION_POLL_INTERVAL_SECONDS", "10"))


class RecomputeSegmentsConfig(Config):
    """Op config for ``recompute_segments_op``. ``tenant_id`` is optional so
    the default (no run_config, e.g. ``segmentation_poll_sensor``'s
    ``RunRequest()``) still recomputes across ALL tenants -- customer360-api's
    on-demand ``POST /segments/admin/recompute-all`` passes ``tenant_id`` via
    run_config to scope a run to the caller's own tenant only (see
    core/utils/dagster_client.py)."""

    tenant_id: Optional[str] = None


@op(retry_policy=RetryPolicy(max_retries=2, delay=10))
def recompute_segments_op(context: OpExecutionContext, config: RecomputeSegmentsConfig) -> dict:
    """Recomputes member_count/segmentation_tags for every active segment,
    scoped to ``config.tenant_id`` if provided, otherwise across all tenants
    (see segmentation.recompute.recompute_all_active_segments)."""
    context.log.info("segmentation job: started (tenant_id=%s)", config.tenant_id or "ALL")
    summary = recompute_all_active_segments(tenant_id=config.tenant_id)
    context.log.info(
        "segmentation job: done (tenant_id=%s, segments_processed=%d, segments_skipped=%d, total_members=%d)",
        summary.get("tenant_id") or "ALL",
        summary["segments_processed"],
        summary["segments_skipped"],
        summary["total_members"],
    )
    return summary


@job(name="segmentation_job")
def segmentation_job() -> None:
    recompute_segments_op()


@sensor(
    job=segmentation_job,
    minimum_interval_seconds=POLL_INTERVAL_SECONDS,
    default_status=DefaultSensorStatus.RUNNING,
)
def segmentation_poll_sensor(context: SensorEvaluationContext):
    """Every ``SEGMENTATION_POLL_INTERVAL_SECONDS`` (default 10s), checks
    whether any ``cdp_master_profiles`` row was created/updated since the
    sensor's cursor (the last successful check), and if so requests a
    ``segmentation_job`` run and advances the cursor to now.

    This is what ties "a profile was created/updated" to "segments get
    recomputed" without a real-time per-row trigger: at most one poll
    interval of staleness, and a no-op (skip) tick whenever nothing changed,
    so idle tenants don't pay for constant full-table recomputes.
    """
    since_iso = context.cursor or None
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        changed_count = count_recently_changed_master_profiles(since_iso)
    except Exception as exc:  # noqa: BLE001 - keep the sensor alive; just skip this tick.
        context.log.error(f"segmentation sensor: DB check failed: {exc}")
        return SkipReason(f"DB check failed: {exc}")

    context.update_cursor(now_iso)

    if changed_count <= 0:
        return SkipReason("No cdp_master_profiles changes since last check")

    context.log.info(
        f"segmentation sensor: {changed_count} profile(s) changed since last check, requesting run"
    )
    return RunRequest()


defs = Definitions(
    jobs=[segmentation_job],
    sensors=[segmentation_poll_sensor],
)

