"""Placeholder Dagster job for the Scoring service (backend-system/scoring).

Future home of the "score all profiles for every metric" service (Lead /
Churn / CLV / CX / Data Quality scoring against `cdp_master_profiles` --
see the scoring-model metadata columns already reserved in
`database-schema.sql`). No real business logic lives here yet -- this file
only wires up a job/op skeleton so the Dagster workspace
(`../workspace.yaml`) already has a `scoring` code location ready for that
logic to be dropped in.

Run from `backend-system/`: `dagster dev -w workspace.yaml` to see this job
(alongside `identity_resolution`/`segmentation`/`analytics`) in the Dagster
UI.
"""

import os
import time

from dagster import Definitions, OpExecutionContext, job, op

PLACEHOLDER_SLEEP_SECONDS = int(os.environ.get("SCORING_PLACEHOLDER_SLEEP_SECONDS", "2"))


@op
def scoring_placeholder_op(context: OpExecutionContext) -> None:
    """Stand-in for the real profile-scoring op: logs started -> sleep -> done
    so the job is runnable/observable end-to-end before real logic exists."""
    context.log.info("scoring job: started")
    time.sleep(PLACEHOLDER_SLEEP_SECONDS)
    context.log.info("scoring job: done")


@job(name="scoring_job")
def scoring_job() -> None:
    scoring_placeholder_op()


defs = Definitions(jobs=[scoring_job])
