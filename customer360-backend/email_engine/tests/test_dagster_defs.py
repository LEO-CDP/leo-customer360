"""Dagster wiring tests for email_engine_job -- runs in-process with
send_campaign mocked (mirrors segmentation/tests/test_dagster_defs.py)."""

from unittest.mock import MagicMock

import dagster_defs


def _run_config(campaign_id="c1", tenant_id="t1"):
    return {"ops": {"send_campaign_op": {"config": {"campaign_id": campaign_id, "tenant_id": tenant_id}}}}


def test_email_engine_job_runs_and_returns_summary(monkeypatch):
    summary = {"campaign_id": "c1", "sent": 3, "failed": 0, "skipped": 1}
    monkeypatch.setattr(dagster_defs, "send_campaign", MagicMock(return_value=summary))

    result = dagster_defs.email_engine_job.execute_in_process(run_config=_run_config())

    assert result.success
    assert result.output_for_node("send_campaign_op") == summary
    call = dagster_defs.send_campaign.call_args
    assert call.args == ("c1", "t1")
    assert callable(call.kwargs["log"])
