"""Dagster definitions for periodic Customer 360 tracking-log analytics."""

import os
import sys

# Dagster's python_file loader does not add this file's directory to sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dagster import (  # noqa: E402
    DefaultScheduleStatus,
    DagsterRunStatus,
    Definitions,
    OpExecutionContext,
    RetryPolicy,
    ScheduleDefinition,
    ScheduleEvaluationContext,
    RunsFilter,
    job,
    op,
)

from source_analytics.tracking_log_aggregation import process_tracking_logs  # noqa: E402


@op(retry_policy=RetryPolicy(max_retries=2, delay=30))
def aggregate_tracking_logs_op(context: OpExecutionContext) -> dict[str, int]:
    """Count hourly S3 JSONL logs and update Redis and source totals."""
    context.log.info("analytics job: tracking-log aggregation started")
    summary = process_tracking_logs(run_id=context.run_id, log=context.log.info)
    context.log.info(
        "analytics job: tracking-log aggregation done "
        "(sources=%d, skipped_running=%d, objects=%d, events_added=%d)",
        summary["sources_processed"],
        summary["sources_skipped_running"],
        summary["objects_processed"],
        summary["events_added"],
    )
    return summary


@job(name="analytics_job", tags={"backend_job": "analytics"})
def analytics_job() -> None:
    aggregate_tracking_logs_op()


def _analytics_run_active(context: ScheduleEvaluationContext) -> bool:
    active_statuses = [
        DagsterRunStatus.NOT_STARTED,
        DagsterRunStatus.QUEUED,
        DagsterRunStatus.STARTING,
        DagsterRunStatus.STARTED,
        DagsterRunStatus.CANCELING,
    ]
    return bool(
        context.instance.get_runs(
            filters=RunsFilter(job_name="analytics_job", statuses=active_statuses),
            limit=1,
        )
    )


analytics_hourly_schedule = ScheduleDefinition(
    name="analytics_hourly_schedule",
    job=analytics_job,
    cron_schedule="*/3 * * * *",
    execution_timezone="GMT",
    default_status=DefaultScheduleStatus.RUNNING,
    should_execute=lambda context: not _analytics_run_active(context),
)


defs = Definitions(jobs=[analytics_job], schedules=[analytics_hourly_schedule])
