"""Placeholder Dagster job for the Campaign Orchestration service
(backend-system/campaign_orchestration).

Future home of external/inbound data synchronization pipelines (e.g.
pulling CRM/warehouse exports into ``cdp_raw_profiles_stage``, or pushing
resolved profiles out to downstream systems). No real business logic lives
here yet -- this file only wires up a job/op skeleton so the Dagster
workspace (``../workspace.yaml``) already has a ``campaign_orchestration`` code location
ready for that logic to be dropped in, and so
``customer360-api/core/utils/dagster_client.py``'s ``CampaignOrchestrationDagsterService``
has a real job to submit against.

Run from `backend-system/`: `dagster dev -w workspace.yaml` to see this job
(alongside `identity_resolution`/`scoring`/`segmentation`/`analytics`) in
the Dagster UI.
"""

import os
import time

from dagster import Definitions, OpExecutionContext, job, op

PLACEHOLDER_SLEEP_SECONDS = int(os.environ.get("CAMPAIGN_ORCHESTRATION_PLACEHOLDER_SLEEP_SECONDS", "2"))


@op
def run_campaign(context: OpExecutionContext) -> None:
    """Stand-in for the real campaign orchestration op: logs started -> sleep -> done so
    the job is runnable/observable end-to-end before real logic exists."""
    context.log.info("run_campaign op: started")
    time.sleep(PLACEHOLDER_SLEEP_SECONDS)
    context.log.info("run_campaign op: done")


@job(name="campaign_orchestration_job")
def campaign_orchestration_job() -> None:
    run_campaign()


defs = Definitions(jobs=[campaign_orchestration_job])
