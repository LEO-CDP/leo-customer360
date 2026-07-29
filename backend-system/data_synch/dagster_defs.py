"""Placeholder Dagster job for the Data Synch service
(backend-system/data_synch).

Future home of external/inbound data synchronization pipelines (e.g.
pulling CRM/warehouse exports into ``cdp_raw_profiles_stage``, or pushing
resolved profiles out to downstream systems). No real business logic lives
here yet -- this file only wires up a job/op skeleton so the Dagster
workspace (``../workspace.yaml``) already has a ``data_synch`` code location
ready for that logic to be dropped in, and so
``customer360-api/core/utils/dagster_client.py``'s ``DataSynchDagsterService``
has a real job to submit against.

Run from `backend-system/`: `dagster dev -w workspace.yaml` to see this job
(alongside `identity_resolution`/`scoring`/`segmentation`/`analytics`) in
the Dagster UI.
"""

import os
import time

from dagster import Definitions, OpExecutionContext, job, op

PLACEHOLDER_SLEEP_SECONDS = int(os.environ.get("DATA_SYNCH_PLACEHOLDER_SLEEP_SECONDS", "2"))


@op
def data_synch_placeholder_op(context: OpExecutionContext) -> None:
    """Stand-in for the real data-sync op: logs started -> sleep -> done so
    the job is runnable/observable end-to-end before real logic exists."""
    context.log.info("data_synch job: started")
    time.sleep(PLACEHOLDER_SLEEP_SECONDS)
    context.log.info("data_synch job: done")


@job(name="data_synch_job")
def data_synch_job() -> None:
    data_synch_placeholder_op()


defs = Definitions(jobs=[data_synch_job])
