"""Outbound email send pipeline for the marketing flow.

Driven by ``../dagster_defs.py``: given one Approved campaign + Approved
template, it resolves the segment's members, renders per recipient, dispatches
through a pluggable adapter (mock default, SMTP when configured), and writes an
idempotent ``cdp_campaign_dispatch_logs`` row per recipient. Only reads Approved
rows, so it is independent of who authored them.
"""
