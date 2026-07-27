"""Placeholder Dagster job for the Analytics service
(backend-system/analytics).

Future home of the "build reporting & update report table" pipeline
(materializing/refreshing reporting tables consumed by
`customer360-api`'s `/api/v1/reporting` endpoints). No real business logic
lives here yet -- this file only wires up a job/op skeleton so the Dagster
workspace (`../workspace.yaml`) already has an `analytics` code location
ready for that logic to be dropped in.

Run from `backend-system/`: `dagster dev -w workspace.yaml` to see this job
(alongside `identity_resolution`/`scoring`/`segmentation`) in the Dagster
UI.
"""

import os
import time

from dagster import Definitions, OpExecutionContext, job, op

PLACEHOLDER_SLEEP_SECONDS = int(os.environ.get("ANALYTICS_PLACEHOLDER_SLEEP_SECONDS", "2"))


@op
def analytics_placeholder_op(context: OpExecutionContext) -> None:
    """Stand-in for the real reporting-table-refresh op: logs started ->
    sleep -> done so the job is runnable/observable end-to-end before real
    logic exists."""
    context.log.info("analytics job: started")
    time.sleep(PLACEHOLDER_SLEEP_SECONDS)
    context.log.info("analytics job: done")


@job(name="analytics_job")
def analytics_job() -> None:
    analytics_placeholder_op()


defs = Definitions(jobs=[analytics_job])
