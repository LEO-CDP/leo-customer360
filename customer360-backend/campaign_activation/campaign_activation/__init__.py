"""Campaign activation orchestration.

Driven by ``../dagster_defs.py``: validates a campaign is Approved, snapshots
the target segment, marks the campaign Running, then hands off to the email send
by submitting ``email_engine_job`` (see ``triggers.py``). Only reads Approved
rows, so it is independent of who authored them.
"""
