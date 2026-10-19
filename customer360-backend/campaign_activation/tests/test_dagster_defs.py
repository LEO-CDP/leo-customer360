"""Dagster wiring tests for the workflow-only campaign activation service."""

from unittest.mock import MagicMock

import dagster_defs
from campaign_activation import triggers


def _run_config(campaign_id="c1", tenant_id="t1", restart=False):
    return {
        "ops": {
            "activate_campaign_op": {
                "config": {
                    "campaign_id": campaign_id,
                    "tenant_id": tenant_id,
                    "restart": restart,
                }
            }
        }
    }


def test_campaign_activation_job_runs_and_returns_summary(monkeypatch):
    summary = {"campaign_id": "c1", "status": "Running", "workflow_run_id": "run-xyz"}
    monkeypatch.setattr(dagster_defs, "activate_campaign", MagicMock(return_value=summary))

    result = dagster_defs.campaign_activation_job.execute_in_process(run_config=_run_config())

    assert result.success
    assert result.output_for_node("activate_campaign_op") == summary
    dagster_defs.activate_campaign.assert_called_once_with(
        "c1", "t1", restart=False, expected_execution_id=None,
        log=dagster_defs.activate_campaign.call_args.kwargs["log"],
    )


def test_campaign_activation_job_forwards_restart_flag(monkeypatch):
    activate = MagicMock(return_value={"status": "Running"})
    monkeypatch.setattr(dagster_defs, "activate_campaign", activate)

    result = dagster_defs.campaign_activation_job.execute_in_process(
        run_config=_run_config(restart=True)
    )

    assert result.success
    activate.assert_called_once()
    assert activate.call_args.kwargs["restart"] is True


def test_campaign_workflow_job_runs_and_returns_summary(monkeypatch):
    summary = {"campaign_id": "c1", "profiles": 4, "sent": 3, "applied": 1, "errors": 0}
    monkeypatch.setattr(dagster_defs, "run_campaign_workflow", MagicMock(return_value=summary))

    result = dagster_defs.campaign_workflow_job.execute_in_process(
        run_config={
            "ops": {
                "run_campaign_workflow_op": {
                    "config": {"campaign_id": "c1", "tenant_id": "t1", "execution_id": "e1"}
                }
            }
        }
    )

    assert result.success
    assert result.output_for_node("run_campaign_workflow_op") == summary
    assert dagster_defs.run_campaign_workflow.call_args.kwargs == {
        "campaign_id": "c1",
        "tenant_id": "t1",
        "run_id": result.run_id,
        "execution_id": "e1",
    }


def test_campaign_workflow_job_accepts_legacy_config_without_execution_id(monkeypatch):
    monkeypatch.setattr(
        dagster_defs, "run_campaign_workflow", MagicMock(return_value={"profiles": 0})
    )
    result = dagster_defs.campaign_workflow_job.execute_in_process(
        run_config={
            "ops": {
                "run_campaign_workflow_op": {
                    "config": {"campaign_id": "c1", "tenant_id": "t1"}
                }
            }
        }
    )
    assert result.success
    assert dagster_defs.run_campaign_workflow.call_args.kwargs["execution_id"] is None


def test_trigger_campaign_workflow_job_submits_workflow_config(monkeypatch):
    fake_client = MagicMock()
    fake_client.submit_job_execution.return_value = "workflow-run-1"
    monkeypatch.setattr(triggers, "DagsterGraphQLClient", MagicMock(return_value=fake_client))

    run_id = triggers.trigger_campaign_workflow_job(
        "c1", "t1", execution_id="e1", log=lambda *_: None
    )

    assert run_id == "workflow-run-1"
    kwargs = fake_client.submit_job_execution.call_args.kwargs
    assert kwargs["repository_location_name"] == "campaign_activation"
    assert kwargs["run_config"]["ops"]["run_campaign_workflow_op"]["config"] == {
        "campaign_id": "c1",
        "tenant_id": "t1",
        "execution_id": "e1",
    }
