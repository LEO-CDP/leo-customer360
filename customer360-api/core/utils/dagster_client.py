"""OOP wrapper around ``dagster_graphql.DagsterGraphQLClient`` for triggering
``backend-system``'s Dagster jobs asynchronously from the API process.

Why this exists: several backend-system pipelines (segment membership
recompute, identity resolution, scoring, ...) do full-table scans against
``cdp_master_profiles``, which can hold 1M+ rows in production. Running that
kind of work synchronously inside an HTTP request handler would block an API
worker for as long as the scan takes and risks request-timeout failures
under load. Instead, this module submits a run of the target service's
already-existing Dagster job (``backend-system/<service>/dagster_defs.py``)
to the Dagster webserver's GraphQL API and returns immediately with a
``run_id`` -- the actual work executes out-of-process in a Dagster run
worker, tracked/retried by Dagster itself, and progress can be polled via
``get_status()``.

customer360-api and backend-system/* are separate deployables (see
backend-system/README.md's "Independent code locations" section) -- this
module only talks to the Dagster webserver over HTTP/GraphQL, it never
imports any service's business-logic package directly.

Layout:
  - ``DagsterJobTriggerError`` -- raised for any submit/status failure;
    callers should turn this into a clean HTTP error response (503) rather
    than a raw 500.
  - ``DagsterService`` -- base class wrapping ONE Dagster code location's
    default job: knows how to ``submit()`` a run and ``get_status()`` a
    previously submitted run_id. Subclassed once per backend-system service.
  - One subclass per backend-system code location, each adding
    domain-specific convenience methods on top of ``submit()``:
    ``AnalyticsDagsterService``, ``IdentityResolutionDagsterService``,
    ``ScoringDagsterService``, ``SegmentationDagsterService``,
    ``DataSynchDagsterService``, ``EmailEngineDagsterService``,
    ``NotificationEngineDagsterService``.
  - ``DagsterClient`` -- facade exposing one attribute per service
    (``dagster_client.segmentation``, ``dagster_client.analytics``, ...).
    The module-level ``dagster_client`` singleton is the intended entry
    point for the rest of the codebase (mirrors the ``core.config.settings``
    singleton pattern).

``scoring``/``data_synch``/``email_engine``/``notification_engine`` currently
wrap placeholder Dagster jobs. ``analytics`` now wraps the hourly data-source
tracking-log aggregation job; all service classes and job/location settings
remain centralized here.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from dagster_graphql import DagsterGraphQLClient, DagsterGraphQLClientError

from core.config import settings

logger = logging.getLogger(__name__)

# Coarse status buckets surfaced to API clients, collapsing Dagster's more
# granular DagsterRunStatus values (QUEUED/NOT_STARTED/MANAGED/STARTING/
# STARTED -> "running") so callers don't need to know Dagster's enum.
_RUNNING_STATUSES = {"QUEUED", "NOT_STARTED", "MANAGED", "STARTING", "STARTED"}
_SUCCESS_STATUSES = {"SUCCESS"}
_FAILURE_STATUSES = {"FAILURE", "CANCELING", "CANCELED"}

# Richer run-detail query than the client library's own ``get_run_status``
# (which only returns ``status``) -- adds start/end/update timestamps and
# step success/failure counts in the SAME round trip, so the frontend can
# show e.g. "Failed -- 2 of 5 steps failed, ran for 42s" instead of just
# "failure". Deliberately does NOT walk the run's full event log (via
# ``eventConnection``) to extract the exact failing step's exception
# message -- for a long-running/high-volume job that would mean paging
# through a potentially large number of events with no guarantee the
# failure event is within a bounded page, which is a poor cost/reliability
# trade-off for a status-poll endpoint. ``stepsFailed`` + ``status`` is
# enough to point an operator at the Dagster UI's run page for the full
# stack trace.
_RUN_DETAIL_QUERY = """
query GraphQLClientGetRunDetail($runId: ID!) {
  pipelineRunOrError(runId: $runId) {
    __typename
    ... on Run {
      status
      startTime
      endTime
      updateTime
      stats {
        __typename
        ... on RunStatsSnapshot {
          stepsSucceeded
          stepsFailed
        }
        ... on PythonError {
          message
        }
      }
    }
    ... on RunNotFoundError {
      message
    }
    ... on PythonError {
      message
    }
  }
}
"""


class DagsterJobTriggerError(Exception):
    """Raised when a job run could not be submitted to (or queried from)
    Dagster -- e.g. the webserver is unreachable, or the job/code location
    isn't registered. Callers should turn this into a clean HTTP error
    response rather than a raw 500."""


def _default_client() -> DagsterGraphQLClient:
    return DagsterGraphQLClient(settings.dagster_graphql_host, port_number=settings.dagster_graphql_port)


def _epoch_to_iso(value: Optional[float]) -> Optional[str]:
    """Converts a Dagster GraphQL epoch-seconds timestamp (``startTime``/
    ``endTime``/``updateTime``) to an ISO 8601 UTC string, or ``None`` if the
    run hasn't reached that point yet (Dagster returns ``null`` for e.g.
    ``endTime`` on a still-running run)."""
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


class DagsterService:
    """Base wrapper around a single Dagster code location's default job.

    Subclasses set ``job_name``/``location_name``/``repository_name`` (via
    their own ``__init__``) and add domain-specific convenience methods that
    build the right ``run_config``/``tags`` before delegating to
    ``submit()``. ``client_factory`` is only overridden in tests (to avoid
    a real ``DagsterGraphQLClient``/HTTP connection).
    """

    def __init__(
        self,
        job_name: str,
        location_name: str,
        repository_name: str = "__repository__",
        client_factory: Callable[[], DagsterGraphQLClient] = _default_client,
    ) -> None:
        self.job_name = job_name
        self.location_name = location_name
        self.repository_name = repository_name
        self._client_factory = client_factory

    def _client(self) -> DagsterGraphQLClient:
        return self._client_factory()

    def submit(
        self,
        run_config: Optional[dict[str, Any]] = None,
        tags: Optional[dict[str, str]] = None,
        op_selection: Optional[list[str]] = None,
    ) -> str:
        """Submits a new run of this service's job and returns the run_id
        immediately, without waiting for it to finish."""
        try:
            run_id = self._client().submit_job_execution(
                self.job_name,
                repository_location_name=self.location_name,
                repository_name=self.repository_name,
                run_config=run_config,
                tags=tags,
                op_selection=op_selection,
            )
        except DagsterGraphQLClientError as exc:
            logger.exception("Dagster rejected %s submission", self.job_name)
            raise DagsterJobTriggerError(f"Dagster rejected {self.job_name} submission: {exc}") from exc
        except Exception as exc:  # connection refused, DNS failure, timeout, ...
            logger.exception("Could not reach Dagster webserver to submit %s", self.job_name)
            raise DagsterJobTriggerError(
                f"Could not reach Dagster webserver at "
                f"{settings.dagster_graphql_host}:{settings.dagster_graphql_port}: {exc}"
            ) from exc

        logger.info("Submitted %s to Dagster (run_id=%s, tags=%s)", self.job_name, run_id, tags)
        return run_id

    def get_status(self, run_id: str) -> dict[str, Any]:
        """Returns run status/detail for a previously submitted run_id::

            {
                "run_id": str,
                "raw_status": <DagsterRunStatus value, e.g. "STARTED">,
                "status": "running" | "success" | "failure",
                "start_time": <ISO 8601 UTC string, or None if not yet started>,
                "end_time": <ISO 8601 UTC string, or None if not yet finished>,
                "duration_seconds": <float, or None if not yet finished>,
                "steps_succeeded": <int, or None if stats unavailable>,
                "steps_failed": <int, or None if stats unavailable>,
            }

        Distinguishes "run not found" from generic connectivity/webserver
        errors in the raised ``DagsterJobTriggerError`` message, so callers
        /logs can tell a stale/typo'd run_id apart from Dagster being down.
        """
        try:
            res_data = self._client()._execute(_RUN_DETAIL_QUERY, {"runId": run_id})
        except Exception as exc:
            logger.exception("Could not fetch Dagster run status for run_id=%s", run_id)
            raise DagsterJobTriggerError(f"Could not fetch run status for '{run_id}': {exc}") from exc

        query_result = res_data["pipelineRunOrError"]
        result_type = query_result["__typename"]
        if result_type not in ("Run", "PipelineRun"):
            # RunNotFoundError / PythonError -- surface Dagster's own message
            # rather than a generic "could not fetch" wrapper.
            raise DagsterJobTriggerError(f"{result_type} fetching status for '{run_id}': {query_result['message']}")

        raw_status = query_result["status"]
        if raw_status in _SUCCESS_STATUSES:
            status = "success"
        elif raw_status in _FAILURE_STATUSES:
            status = "failure"
        else:
            status = "running"

        start_time = query_result.get("startTime")
        end_time = query_result.get("endTime")
        duration_seconds = (end_time - start_time) if (start_time is not None and end_time is not None) else None

        stats = query_result.get("stats") or {}
        steps_succeeded = stats.get("stepsSucceeded") if stats.get("__typename") == "RunStatsSnapshot" else None
        steps_failed = stats.get("stepsFailed") if stats.get("__typename") == "RunStatsSnapshot" else None

        return {
            "run_id": run_id,
            "raw_status": raw_status,
            "status": status,
            "start_time": _epoch_to_iso(start_time),
            "end_time": _epoch_to_iso(end_time),
            "duration_seconds": duration_seconds,
            "steps_succeeded": steps_succeeded,
            "steps_failed": steps_failed,
        }


class AnalyticsDagsterService(DagsterService):
    """backend-system/analytics -- data-source tracking-log aggregation."""

    def __init__(self) -> None:
        super().__init__(
            job_name=settings.dagster_analytics_job_name,
            location_name=settings.dagster_analytics_location_name,
            repository_name=settings.dagster_analytics_repository_name,
        )

    def process_tracking_logs(self) -> str:
        """Triggers one asynchronous data-source tracking-log aggregation."""
        return self.submit(tags={"trigger_reason": "manual_api"})

    def refresh_reports(self) -> str:
        """Backward-compatible alias for the tracking-log aggregation run."""
        return self.process_tracking_logs()


class IdentityResolutionDagsterService(DagsterService):
    """backend-system/identity_resolution -- Customer Identity Resolution
    (CIR) batch cycle."""

    def __init__(self) -> None:
        super().__init__(
            job_name=settings.dagster_identity_resolution_job_name,
            location_name=settings.dagster_identity_resolution_location_name,
            repository_name=settings.dagster_identity_resolution_repository_name,
        )

    def run_resolution(self) -> str:
        """Triggers one identity-resolution cycle (drains
        ``cdp_raw_profiles_stage`` until empty)."""
        return self.submit()

    def recompute_personas(
        self,
        trigger_reason: str,
        tenant_id: Optional[str] = None,
        persona_archetype_id: Optional[str] = None,
    ) -> str:
        """Triggers ``identity_resolution_job`` after a
        ``cdp_persona_archetypes`` row is created/edited. When
        ``persona_archetype_id`` and ``tenant_id`` are provided, the job
        receives them as run config and refreshes only that archetype's
        active ``matched_profile_count``; the values are also retained as
        run tags for observability.

        Without ``persona_archetype_id``, this preserves the existing full
        CIR job behavior and submits tags only.
        """
        tags = {"trigger_reason": trigger_reason}
        run_config = None
        if tenant_id:
            tags["tenant_id"] = str(tenant_id)
        if persona_archetype_id:
            if not tenant_id:
                raise ValueError("tenant_id is required when persona_archetype_id is provided")
            tags["persona_archetype_id"] = str(persona_archetype_id)
            run_config = {
                "ops": {
                    "resolve_identities_op": {
                        "config": {
                            "tenant_id": str(tenant_id),
                            "persona_archetype_id": str(persona_archetype_id),
                        }
                    }
                }
            }
        return self.submit(run_config=run_config, tags=tags)


class ScoringDagsterService(DagsterService):
    """backend-system/scoring -- profile scoring run (placeholder job today,
    see backend-system/scoring/dagster_defs.py)."""

    def __init__(self) -> None:
        super().__init__(
            job_name=settings.dagster_scoring_job_name,
            location_name=settings.dagster_scoring_location_name,
            repository_name=settings.dagster_scoring_repository_name,
        )

    def run_scoring(self) -> str:
        """Triggers a scoring run across profiles (Lead/Churn/CLV/CX/Data
        Quality models)."""
        return self.submit()


class SegmentationDagsterService(DagsterService):
    """backend-system/segmentation -- segment membership recompute.

    ``refresh``/``create``/``update`` all submit the SAME underlying
    ``segmentation_job`` -- there is no separate Dagster op per action,
    since a segment row's create/update itself is a synchronous DB write
    (see ``core/crud/segmentation.py``); what these need Dagster for is the
    potentially-expensive membership recompute that should follow. What
    differs per method is the ``trigger_reason`` tag attached to the run
    (useful for filtering run history in the Dagster UI) and the docstring
    describing when to call it.

    All methods require a ``tenant_id`` and scope the run to that tenant.
    ``refresh`` can optionally pass ``segment_id``; ``create`` and ``update``
    always pass it so only the changed segment is recomputed. All values are sent via
    ``RecomputeSegmentsConfig`` (see
    ``backend-system/segmentation/dagster_defs.py``) -- this can never
    accidentally trigger a cross-tenant/global recompute from an on-demand
    API call. ``tenant_id`` must be the caller's own tenant
    (``request.state.tenant_id``), enforced by the router, not this class.
    """

    def __init__(self) -> None:
        super().__init__(
            job_name=settings.dagster_segmentation_job_name,
            location_name=settings.dagster_segmentation_location_name,
            repository_name=settings.dagster_segmentation_repository_name,
        )

    def _submit_scoped_recompute(
        self,
        tenant_id: str,
        trigger_reason: str,
        segment_id: Optional[str] = None,
    ) -> str:
        if not tenant_id:
            raise ValueError("tenant_id is required to trigger a segmentation recompute")
        if segment_id is not None and not segment_id:
            raise ValueError("segment_id cannot be empty")
        config = {"tenant_id": str(tenant_id)}
        tags = {"trigger_reason": trigger_reason}
        if segment_id is not None:
            config["segment_id"] = str(segment_id)
        run_config = {"ops": {"recompute_segments_op": {"config": config}}}
        return self.submit(run_config=run_config, tags=tags)

    def refresh(self, tenant_id: str, segment_id: Optional[str] = None) -> str:
        """Recomputes member_count/segmentation_tags for every active
        segment belonging to ``tenant_id`` (the admin UI's "Refresh"
        button), or only ``segment_id`` when provided."""
        return self._submit_scoped_recompute(
            tenant_id,
            trigger_reason="refresh",
            segment_id=segment_id,
        )

    def create(self, tenant_id: str, segment_id: str) -> str:
        """Recomputes membership for ``tenant_id`` after a new segment was
        created, so the new segment's member_count/tags are populated
        without waiting for the next scheduled poll."""
        return self._submit_scoped_recompute(tenant_id, trigger_reason="create", segment_id=segment_id)

    def update(self, tenant_id: str, segment_id: str) -> str:
        """Recomputes membership for ``tenant_id`` after a segment's rules
        were edited, so member_count/tags reflect the new rules
        immediately."""
        return self._submit_scoped_recompute(tenant_id, trigger_reason="update", segment_id=segment_id)


class DataSynchDagsterService(DagsterService):
    """backend-system/data_synch -- external data synchronization
    (placeholder job today, see backend-system/data_synch/dagster_defs.py)."""

    def __init__(self) -> None:
        super().__init__(
            job_name=settings.dagster_data_synch_job_name,
            location_name=settings.dagster_data_synch_location_name,
            repository_name=settings.dagster_data_synch_repository_name,
        )

    def run_sync(self) -> str:
        """Triggers a data synchronization run."""
        return self.submit()


class EmailEngineDagsterService(DagsterService):
    """backend-system/email_engine -- outbound email campaign/journey
    execution (placeholder job today, see
    backend-system/email_engine/dagster_defs.py)."""

    def __init__(self) -> None:
        super().__init__(
            job_name=settings.dagster_email_engine_job_name,
            location_name=settings.dagster_email_engine_location_name,
            repository_name=settings.dagster_email_engine_repository_name,
        )

    def send_campaign(self) -> str:
        """Triggers an email campaign send run."""
        return self.submit()


class NotificationEngineDagsterService(DagsterService):
    """backend-system/notification_engine -- outbound push/SMS/in-app
    notification dispatch (placeholder job today, see
    backend-system/notification_engine/dagster_defs.py)."""

    def __init__(self) -> None:
        super().__init__(
            job_name=settings.dagster_notification_engine_job_name,
            location_name=settings.dagster_notification_engine_location_name,
            repository_name=settings.dagster_notification_engine_repository_name,
        )

    def dispatch(self) -> str:
        """Triggers a notification dispatch run."""
        return self.submit()


class DagsterClient:
    """Facade grouping one ``DagsterService`` per backend-system code
    location. Use the module-level ``dagster_client`` singleton below
    rather than instantiating this directly.

    Usage::

        from core.utils.dagster_client import DagsterJobTriggerError, dagster_client

        run_id = dagster_client.segmentation.refresh(tenant_id=str(tenant_id))
        status = dagster_client.segmentation.get_status(run_id)
    """

    def __init__(self) -> None:
        self.analytics = AnalyticsDagsterService()
        self.identity_resolution = IdentityResolutionDagsterService()
        self.scoring = ScoringDagsterService()
        self.segmentation = SegmentationDagsterService()
        self.data_synch = DataSynchDagsterService()
        self.email_engine = EmailEngineDagsterService()
        self.notification_engine = NotificationEngineDagsterService()


dagster_client = DagsterClient()
