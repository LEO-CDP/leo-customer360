"""Placeholder Dagster job for the Email Engine service
(backend-system/email_engine).

Future home of outbound email campaign/journey execution (rendering +
sending templated emails to segment members, tracking opens/clicks back
into ``customer360``). No real business logic lives here yet -- this file
only wires up a job/op skeleton so the Dagster workspace
(``../workspace.yaml``) already has an ``email_engine`` code location ready
for that logic to be dropped in, and so
``customer360-api/core/utils/dagster_client.py``'s ``EmailEngineDagsterService``
has a real job to submit against.

Run from `backend-system/`: `dagster dev -w workspace.yaml` to see this job
(alongside `identity_resolution`/`scoring`/`segmentation`/`analytics`) in
the Dagster UI.
"""

import os
import time

from dagster import Definitions, OpExecutionContext, job, op

PLACEHOLDER_SLEEP_SECONDS = int(os.environ.get("EMAIL_ENGINE_PLACEHOLDER_SLEEP_SECONDS", "2"))


@op
def email_engine_placeholder_op(context: OpExecutionContext) -> None:
    """Stand-in for the real campaign-send op: logs started -> sleep -> done
    so the job is runnable/observable end-to-end before real logic exists."""
    context.log.info("email_engine job: started")
    time.sleep(PLACEHOLDER_SLEEP_SECONDS)
    context.log.info("email_engine job: done")


@job(name="email_engine_job")
def email_engine_job() -> None:
    email_engine_placeholder_op()


defs = Definitions(jobs=[email_engine_job])
