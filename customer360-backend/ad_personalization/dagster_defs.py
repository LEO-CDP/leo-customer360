"""Dagster definitions for Customer 360 ad personalisation batch jobs.

Periodic, per-tenant work for the ad personalisation engine (profile
aggregation, ad statistics, similar-visitor cohort patterns, erasure,
identity linking and partition upkeep). Jobs are added by the tasks in
specs/003-ad-personalization-engine/tasks.md; each one is registered here with
a schedule and the run-active guard used by the analytics location.
"""

import os
import sys

# Dagster's python_file loader does not add this file's directory to sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dagster import (  # noqa: E402
    DagsterRunStatus,
    DefaultScheduleStatus,
    Definitions,
    JobDefinition,
    OpExecutionContext,
    RetryPolicy,
    RunsFilter,
    ScheduleDefinition,
    ScheduleEvaluationContext,
    job,
    op,
)

from ad_personalization.db import ADS_DB_SCHEMA, connect  # noqa: E402
from ad_personalization.ad_stats import refresh_ad_stats  # noqa: E402
from ad_personalization.cohort_patterns import refresh_cohort_patterns  # noqa: E402
from ad_personalization.erasure import process_erasure_requests  # noqa: E402
from ad_personalization.identity_link import link_identities  # noqa: E402
from ad_personalization.partition_maintenance import (  # noqa: E402
    drop_enabled_from_env,
    run_maintenance,
)
from ad_personalization.profile_aggregation import rebuild_profiles  # noqa: E402


def _run_active(context: ScheduleEvaluationContext, job_name: str) -> bool:
    """True while a run of ``job_name`` is queued or in progress."""
    active_statuses = [
        DagsterRunStatus.NOT_STARTED,
        DagsterRunStatus.QUEUED,
        DagsterRunStatus.STARTING,
        DagsterRunStatus.STARTED,
        DagsterRunStatus.CANCELING,
    ]
    return bool(
        context.instance.get_runs(
            filters=RunsFilter(job_name=job_name, statuses=active_statuses),
            limit=1,
        )
    )


def guarded_schedule(name: str, job_def: JobDefinition, cron: str) -> ScheduleDefinition:
    """A schedule that skips a tick while the previous run is still active."""
    return ScheduleDefinition(
        name=name,
        job=job_def,
        cron_schedule=cron,
        execution_timezone="GMT",
        default_status=DefaultScheduleStatus.RUNNING,
        should_execute=lambda context: not _run_active(context, job_def.name),
    )


@op(retry_policy=RetryPolicy(max_retries=2, delay=30))
def partition_maintenance_op(context: OpExecutionContext) -> dict:
    """Create upcoming ad_event / serving_decision partitions and expire old ones.

    Old partitions are only dropped when
    AD_PERSONALIZATION_PARTITION_DROP_ENABLED=true; otherwise the op logs what
    it would drop.
    """
    context.log.info("ad_personalization: partition maintenance started")
    conn = connect()
    try:
        summary = run_maintenance(
            conn,
            schema=ADS_DB_SCHEMA,
            drop_enabled=drop_enabled_from_env(),
            log=context.log.info,
        )
    finally:
        conn.close()
    context.log.info(
        "ad_personalization: partition maintenance done "
        "(created=%d, attached=%d, existing=%d, dropped=%d, would_drop=%d)",
        len(summary["created"]),
        len(summary["attached"]),
        summary["existing"],
        len(summary["dropped"]),
        len(summary["would_drop"]),
    )
    return summary


@job(name="ad_partition_maintenance_job", tags={"backend_job": "ad_personalization"})
def ad_partition_maintenance_job() -> None:
    partition_maintenance_op()


ad_partition_maintenance_schedule = guarded_schedule(
    "ad_partition_maintenance_schedule",
    ad_partition_maintenance_job,
    "0 2 * * *",
)


@op(retry_policy=RetryPolicy(max_retries=2, delay=30))
def profile_aggregation_op(context: OpExecutionContext) -> dict:
    """Rebuild visitor behaviour profiles for every tenant with personalisation on."""
    context.log.info("ad_personalization: profile aggregation started")
    conn = connect()
    try:
        summary = rebuild_profiles(conn, schema=ADS_DB_SCHEMA, log=context.log.info)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    context.log.info("ad_personalization: profile aggregation done (%d tenants)", len(summary))
    return {str(tenant_id): counts for tenant_id, counts in summary.items()}


@job(name="ad_profile_aggregation_job", tags={"backend_job": "ad_personalization"})
def ad_profile_aggregation_job() -> None:
    profile_aggregation_op()


ad_profile_aggregation_schedule = guarded_schedule(
    "ad_profile_aggregation_schedule",
    ad_profile_aggregation_job,
    "10 * * * *",
)


@op(retry_policy=RetryPolicy(max_retries=2, delay=30))
def ad_stats_op(context: OpExecutionContext) -> dict:
    """Recompute the trailing 48 hours of hourly ad statistics."""
    context.log.info("ad_personalization: ad statistics started")
    conn = connect()
    try:
        summary = refresh_ad_stats(conn, schema=ADS_DB_SCHEMA)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    context.log.info(
        "ad_personalization: ad statistics done (deleted=%d, written=%d)",
        summary["deleted"],
        summary["written"],
    )
    return summary


@job(name="ad_stats_job", tags={"backend_job": "ad_personalization"})
def ad_stats_job() -> None:
    ad_stats_op()


ad_stats_schedule = guarded_schedule("ad_stats_schedule", ad_stats_job, "5 * * * *")


@op(retry_policy=RetryPolicy(max_retries=2, delay=30))
def erasure_op(context: OpExecutionContext) -> dict:
    """Complete open data-subject erasure requests (urgent first). Also runnable on demand."""
    context.log.info("ad_personalization: erasure started")
    conn = connect()
    try:
        summary = process_erasure_requests(conn, schema=ADS_DB_SCHEMA, log=context.log.info)
        # Each request is isolated by a savepoint, so a failure is recorded on its
        # own row and the rest of the run still commits.
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    context.log.info(
        "ad_personalization: erasure done (completed=%d, failed=%d, overdue=%d)",
        summary["completed"],
        summary["failed"],
        summary["overdue"],
    )
    return summary


@job(name="ad_erasure_job", tags={"backend_job": "ad_personalization"})
def ad_erasure_job() -> None:
    erasure_op()


ad_erasure_schedule = guarded_schedule("ad_erasure_schedule", ad_erasure_job, "20 * * * *")


@op(retry_policy=RetryPolicy(max_retries=2, delay=30))
def cohort_patterns_op(context: OpExecutionContext) -> dict:
    """Publish anonymous similar-visitor response patterns (cohorts of at least the minimum size)."""
    context.log.info("ad_personalization: cohort patterns started")
    conn = connect()
    try:
        summary = refresh_cohort_patterns(conn, schema=ADS_DB_SCHEMA, log=context.log.info)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    context.log.info("ad_personalization: cohort patterns done (%d tenants)", len(summary))
    return {str(tenant_id): counts for tenant_id, counts in summary.items()}


@job(name="ad_cohort_patterns_job", tags={"backend_job": "ad_personalization"})
def ad_cohort_patterns_job() -> None:
    cohort_patterns_op()


ad_cohort_patterns_schedule = guarded_schedule(
    "ad_cohort_patterns_schedule", ad_cohort_patterns_job, "40 */6 * * *"
)


@op(retry_policy=RetryPolicy(max_retries=2, delay=30))
def identity_link_op(context: OpExecutionContext) -> dict:
    """Share one profile reference across all of a known visitor's devices (cross-device identity)."""
    context.log.info("ad_personalization: identity link started")
    conn = connect()
    try:
        summary = link_identities(conn, schema=ADS_DB_SCHEMA, log=context.log.info)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    context.log.info("ad_personalization: identity link done (%d tenants)", len(summary))
    return {str(tenant_id): counts for tenant_id, counts in summary.items()}


@job(name="ad_identity_link_job", tags={"backend_job": "ad_personalization"})
def ad_identity_link_job() -> None:
    identity_link_op()


ad_identity_link_schedule = guarded_schedule("ad_identity_link_schedule", ad_identity_link_job, "50 * * * *")


defs = Definitions(
    jobs=[
        ad_partition_maintenance_job,
        ad_profile_aggregation_job,
        ad_stats_job,
        ad_erasure_job,
        ad_cohort_patterns_job,
        ad_identity_link_job,
    ],
    schedules=[
        ad_partition_maintenance_schedule,
        ad_profile_aggregation_schedule,
        ad_stats_schedule,
        ad_erasure_schedule,
        ad_cohort_patterns_schedule,
        ad_identity_link_schedule,
    ],
)
