"""Thin wrapper around ``dagster_graphql.DagsterGraphQLClient`` for triggering
``backend-system/segmentation``'s batch ``segmentation_job`` asynchronously
from the API process.

Why this exists: ``cdp_master_profiles`` can hold 1M+ rows in production, and
recomputing every active segment's membership against that table (see
``core/crud/segmentation.recompute_segment_membership``) is a full-table scan
per segment. Doing that synchronously inside an HTTP request handler (the
previous ``POST /segments/admin/recompute-all`` implementation) would block
an API worker for as long as the scan takes and risks request-timeout
failures under load. Instead, this module submits a run of the already
-existing ``segmentation_job`` (``backend-system/segmentation/dagster_defs.py``)
to the Dagster webserver's GraphQL API and returns immediately with a
``run_id`` -- the actual recompute executes out-of-process in a Dagster
run worker, tracked/retried by Dagster itself (see that job's
``RetryPolicy``), and progress can be polled via ``get_job_run_status``.

``trigger_segmentation_recompute_job`` REQUIRES a ``tenant_id`` and passes it
as the op's run_config (``RecomputeSegmentsConfig.tenant_id`` in
``backend-system/segmentation/dagster_defs.py``) -- the on-demand "Refresh"
button must only ever recompute the caller's own tenant, never every
tenant's segments in one run (that global sweep is
``segmentation_poll_sensor``'s job, on its own schedule).

customer360-api and backend-system/segmentation are separate deployables
(see backend-system/README.md's "Independent code locations" section) --
this module only talks to the Dagster webserver over HTTP/GraphQL, it never
imports segmentation's business-logic package directly.
"""

import logging

from dagster_graphql import DagsterGraphQLClient, DagsterGraphQLClientError

from core.config import settings

logger = logging.getLogger(__name__)

# Coarse status buckets surfaced to API clients, collapsing Dagster's more
# granular DagsterRunStatus values (QUEUED/NOT_STARTED/MANAGED/STARTING/
# STARTED -> "running") so the frontend doesn't need to know Dagster's enum.
_RUNNING_STATUSES = {"QUEUED", "NOT_STARTED", "MANAGED", "STARTING", "STARTED"}
_SUCCESS_STATUSES = {"SUCCESS"}
_FAILURE_STATUSES = {"FAILURE", "CANCELING", "CANCELED"}


class DagsterJobTriggerError(Exception):
    """Raised when the segmentation_job run could not be submitted to (or
    queried from) Dagster -- e.g. the webserver is unreachable, or the job/
    code location isn't registered. Callers should turn this into a clean
    HTTP error response rather than a raw 500."""


def _client() -> DagsterGraphQLClient:
    return DagsterGraphQLClient(settings.dagster_graphql_host, port_number=settings.dagster_graphql_port)


def trigger_segmentation_recompute_job(tenant_id: str) -> str:
    """Submits a new run of ``segmentation_job`` scoped to ``tenant_id`` --
    recomputes member_count/segmentation_tags for every ``is_active`` segment
    belonging to that tenant ONLY (see
    ``backend-system/segmentation/segmentation/recompute.py``) -- and returns
    the new run's id immediately, without waiting for it to finish.

    ``tenant_id`` is required and always passed through as run_config so this
    can never accidentally trigger a cross-tenant/global recompute from an
    on-demand API call.
    """
    run_config = {"ops": {"recompute_segments_op": {"config": {"tenant_id": str(tenant_id)}}}}
    try:
        run_id = _client().submit_job_execution(
            settings.dagster_segmentation_job_name,
            repository_location_name=settings.dagster_segmentation_location_name,
            repository_name=settings.dagster_segmentation_repository_name,
            run_config=run_config,
        )
    except DagsterGraphQLClientError as exc:
        logger.exception("Dagster rejected segmentation_job submission")
        raise DagsterJobTriggerError(f"Dagster rejected segmentation_job submission: {exc}") from exc
    except Exception as exc:  # connection refused, DNS failure, timeout, ...
        logger.exception("Could not reach Dagster webserver to submit segmentation_job")
        raise DagsterJobTriggerError(
            f"Could not reach Dagster webserver at "
            f"{settings.dagster_graphql_host}:{settings.dagster_graphql_port}: {exc}"
        ) from exc

    logger.info("Submitted segmentation_job to Dagster (run_id=%s, tenant_id=%s)", run_id, tenant_id)
    return run_id


def get_job_run_status(run_id: str) -> dict[str, str]:
    """Returns ``{"run_id": ..., "raw_status": <DagsterRunStatus value>,
    "status": "running"|"success"|"failure"}`` for a previously submitted
    run_id."""
    try:
        raw_status = _client().get_run_status(run_id).value
    except Exception as exc:
        logger.exception("Could not fetch Dagster run status for run_id=%s", run_id)
        raise DagsterJobTriggerError(f"Could not fetch run status for '{run_id}': {exc}") from exc

    if raw_status in _SUCCESS_STATUSES:
        status = "success"
    elif raw_status in _FAILURE_STATUSES:
        status = "failure"
    else:
        status = "running"

    return {"run_id": run_id, "raw_status": raw_status, "status": status}
