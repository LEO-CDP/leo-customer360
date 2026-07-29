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

``analytics``/``scoring``/``data_synch``/``email_engine``/
``notification_engine`` currently wrap placeholder Dagster jobs (no real
business logic yet, see each service's ``dagster_defs.py``) -- their
service classes and job/location settings already exist here so real logic
can be dropped in behind them without any customer360-api changes.
"""

import logging
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


class DagsterJobTriggerError(Exception):
    """Raised when a job run could not be submitted to (or queried from)
    Dagster -- e.g. the webserver is unreachable, or the job/code location
    isn't registered. Callers should turn this into a clean HTTP error
    response rather than a raw 500."""


def _default_client() -> DagsterGraphQLClient:
    return DagsterGraphQLClient(settings.dagster_graphql_host, port_number=settings.dagster_graphql_port)


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

    def get_status(self, run_id: str) -> dict[str, str]:
        """Returns ``{"run_id": ..., "raw_status": <DagsterRunStatus value>,
        "status": "running"|"success"|"failure"}`` for a previously
        submitted run_id."""
        try:
            raw_status = self._client().get_run_status(run_id).value
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


class AnalyticsDagsterService(DagsterService):
    """backend-system/analytics -- reporting-table refresh (placeholder job
    today, see backend-system/analytics/dagster_defs.py)."""

    def __init__(self) -> None:
        super().__init__(
            job_name=settings.dagster_analytics_job_name,
            location_name=settings.dagster_analytics_location_name,
            repository_name=settings.dagster_analytics_repository_name,
        )

    def refresh_reports(self) -> str:
        """Triggers a refresh of reporting tables consumed by
        ``/api/v1/reporting``."""
        return self.submit()


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

    All three REQUIRE a ``tenant_id`` and always scope the run to it via
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

    def _submit_scoped_recompute(self, tenant_id: str, trigger_reason: str) -> str:
        if not tenant_id:
            raise ValueError("tenant_id is required to trigger a segmentation recompute")
        run_config = {"ops": {"recompute_segments_op": {"config": {"tenant_id": str(tenant_id)}}}}
        return self.submit(run_config=run_config, tags={"trigger_reason": trigger_reason})

    def refresh(self, tenant_id: str) -> str:
        """Recomputes member_count/segmentation_tags for every active
        segment belonging to ``tenant_id`` (the admin UI's "Refresh"
        button)."""
        return self._submit_scoped_recompute(tenant_id, trigger_reason="refresh")

    def create(self, tenant_id: str) -> str:
        """Recomputes membership for ``tenant_id`` after a new segment was
        created, so the new segment's member_count/tags are populated
        without waiting for the next scheduled poll."""
        return self._submit_scoped_recompute(tenant_id, trigger_reason="create")

    def update(self, tenant_id: str) -> str:
        """Recomputes membership for ``tenant_id`` after a segment's rules
        were edited, so member_count/tags reflect the new rules
        immediately."""
        return self._submit_scoped_recompute(tenant_id, trigger_reason="update")


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
