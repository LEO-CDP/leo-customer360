"""Placeholder Dagster job for the Segmentation service
(backend-system/segmentation).

Future home of the "build segments by AI service" pipeline (evaluating/
refreshing `cdp_segments` membership, including LLM/semantic-search-driven
segment definitions over `persona_embedding`/`graph_edges`). No real
business logic lives here yet -- this file only wires up a job/op skeleton
so the Dagster workspace (`../workspace.yaml`) already has a `segmentation`
code location ready for that logic to be dropped in.

Run from `backend-system/`: `dagster dev -w workspace.yaml` to see this job
(alongside `identity_resolution`/`scoring`/`analytics`) in the Dagster UI.
"""

import os
import time

from dagster import Definitions, OpExecutionContext, job, op

PLACEHOLDER_SLEEP_SECONDS = int(os.environ.get("SEGMENTATION_PLACEHOLDER_SLEEP_SECONDS", "2"))


@op
def segmentation_placeholder_op(context: OpExecutionContext) -> None:
    """Stand-in for the real AI segmentation op: logs started -> sleep -> done
    so the job is runnable/observable end-to-end before real logic exists."""
    context.log.info("segmentation job: started")
    time.sleep(PLACEHOLDER_SLEEP_SECONDS)
    context.log.info("segmentation job: done")


@job(name="segmentation_job")
def segmentation_job() -> None:
    segmentation_placeholder_op()


defs = Definitions(jobs=[segmentation_job])
