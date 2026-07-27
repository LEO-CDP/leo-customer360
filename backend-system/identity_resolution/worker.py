"""Continuous Customer Identity Resolution (CIR) worker for containerized
(production) deployments.

Repeatedly executes the Dagster ``identity_resolution_job`` (see
``dagster_defs.py``) in-process, sleeping ``CIR_POLL_INTERVAL_SECONDS``
between cycles, instead of calling
``identity_resolution.daily_job.run_daily_identity_resolution()`` directly.
Every cycle therefore runs through Dagster's execution engine -- tracked as
a Dagster run with per-step logs, timing, and (via the op's retry policy)
automatic retries -- so it's ready to be monitored with a Dagster
webserver/daemon (``dagster dev -w ../workspace.yaml``) without changing
this container's behavior. A single cycle failure (e.g. a transient DB
hiccup) is logged and retried on the next interval rather than crashing the
container, so `restart: unless-stopped` isn't fighting a crash-loop for
routine transient errors.

Set ``DAGSTER_HOME`` to a persistent directory to keep run history across
restarts/until a real Dagster daemon takes over scheduling; without it,
Dagster uses an ephemeral in-memory instance for each ``execute_in_process``
call below.
"""

import logging
import os
import time

from dagster_defs import identity_resolution_job

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = int(os.environ.get("CIR_POLL_INTERVAL_SECONDS", "30"))


def main() -> None:
    logger.info("CIR worker: started (polling every %ss).", POLL_INTERVAL_SECONDS)
    while True:
        result = identity_resolution_job.execute_in_process(raise_on_error=False)
        if result.success:
            processed = result.output_for_node("resolve_identities_op")
            if processed:
                logger.info("CIR worker: cycle done, processed %d raw profile(s).", processed)
        else:
            logger.error("CIR worker: cycle failed; will retry next interval.")
        logger.info("CIR worker: sleeping %ss.", POLL_INTERVAL_SECONDS)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
