"""Dagster wiring + email-engine-trigger tests for campaign_activation (no DB /
no Dagster webserver; the business + GraphQL calls are mocked)."""

from unittest.mock import MagicMock

import dagster_defs
from campaign_activation import triggers


def _run_config(campaign_id="c1", tenant_id="t1"):
    return {"ops": {"activate_campaign_op": {"config": {"campaign_id": campaign_id, "tenant_id": tenant_id}}}}


def test_campaign_activation_job_runs_and_returns_summary(monkeypatch):
    summary = {"campaign_id": "c1", "snapshot_count": 42, "email_engine_run_id": "run-xyz"}
    monkeypatch.setattr(dagster_defs, "activate_campaign", MagicMock(return_value=summary))

    result = dagster_defs.campaign_activation_job.execute_in_process(run_config=_run_config())

    assert result.success
    assert result.output_for_node("activate_campaign_op") == summary
    assert dagster_defs.activate_campaign.call_args.args == ("c1", "t1")


def test_trigger_email_engine_job_submits_with_campaign_run_config(monkeypatch):
    fake_client = MagicMock()
    fake_client.submit_job_execution.return_value = "email-run-1"
    monkeypatch.setattr(triggers, "DagsterGraphQLClient", MagicMock(return_value=fake_client))

    run_id = triggers.trigger_email_engine_job("c1", "t1", log=lambda *_: None)

    assert run_id == "email-run-1"
    kwargs = fake_client.submit_job_execution.call_args.kwargs
    assert kwargs["repository_location_name"] == "email_engine"
    assert kwargs["run_config"]["ops"]["send_campaign_op"]["config"] == {"campaign_id": "c1", "tenant_id": "t1"}


def test_trigger_email_engine_job_wraps_submit_failure(monkeypatch):
    fake_client = MagicMock()
    fake_client.submit_job_execution.side_effect = RuntimeError("webserver down")
    monkeypatch.setattr(triggers, "DagsterGraphQLClient", MagicMock(return_value=fake_client))

    try:
        triggers.trigger_email_engine_job("c1", "t1", log=lambda *_: None)
        assert False, "expected EmailEngineTriggerError"
    except triggers.EmailEngineTriggerError as exc:
        assert "webserver down" in str(exc)
