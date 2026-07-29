"""Placeholder Dagster job for the Notification Engine service
(backend-system/notification_engine).

Future home of outbound push/SMS/in-app notification dispatch (triggered by
segment membership changes, scoring thresholds, etc). No real business
logic lives here yet -- this file only wires up a job/op skeleton so the
Dagster workspace (``../workspace.yaml``) already has a
``notification_engine`` code location ready for that logic to be dropped
in, and so
``customer360-api/core/utils/dagster_client.py``'s
``NotificationEngineDagsterService`` has a real job to submit against.

Run from `backend-system/`: `dagster dev -w workspace.yaml` to see this job
(alongside `identity_resolution`/`scoring`/`segmentation`/`analytics`) in
the Dagster UI.
"""

import os
import time

from dagster import Definitions, OpExecutionContext, job, op

PLACEHOLDER_SLEEP_SECONDS = int(os.environ.get("NOTIFICATION_ENGINE_PLACEHOLDER_SLEEP_SECONDS", "2"))


@op
def notification_engine_placeholder_op(context: OpExecutionContext) -> None:
    """Stand-in for the real notification-dispatch op: logs started -> sleep
    -> done so the job is runnable/observable end-to-end before real logic
    exists."""
    context.log.info("notification_engine job: started")
    time.sleep(PLACEHOLDER_SLEEP_SECONDS)
    context.log.info("notification_engine job: done")


@job(name="notification_engine_job")
def notification_engine_job() -> None:
    notification_engine_placeholder_op()


defs = Definitions(jobs=[notification_engine_job])
