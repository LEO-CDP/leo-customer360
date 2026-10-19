"""Unit tests for the workflow-only campaign activation boundary."""

from unittest.mock import MagicMock

import pytest

from campaign_activation import activation
from campaign_activation.activation import CampaignActivationError, activate_campaign

APPROVED = {
    "campaign_id": "c1",
    "name": "N",
    "approval_status": "Approved",
    "status": "Draft",
    "segment_id": "s1",
    "workflow_id": "flow-1",
    "workflow_version": 1,
    "workflow_execution_id": "9f61d7be-e14d-4c88-9058-f817558af542",
    "workflow_approval_status": "Approved",
}


def _wire(monkeypatch, fetchones):
    cur = MagicMock()
    cur.fetchone.side_effect = list(fetchones)
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    trigger = MagicMock(return_value="workflow-run-1")
    monkeypatch.setattr(activation, "connect", lambda: conn)
    monkeypatch.setattr(activation, "lock_campaign_execution", lambda *_: True)
    monkeypatch.setattr(activation, "trigger_campaign_workflow_job", trigger)
    return conn, cur, trigger


def test_activation_marks_running_and_submits_workflow(monkeypatch):
    conn, cur, trigger = _wire(monkeypatch, [dict(APPROVED), {"segment_tag": "vip"}])

    out = activate_campaign("c1", "t1", log=lambda *_: None)

    assert out == {
        "campaign_id": "c1",
        "tenant_id": "t1",
        "status": "Running",
        "workflow_run_id": "workflow-run-1",
        "execution_id": APPROVED["workflow_execution_id"],
    }
    trigger.assert_called_once_with(
        "c1", "t1",
        execution_id=APPROVED["workflow_execution_id"],
        log=trigger.call_args.kwargs["log"],
    )
    conn.commit.assert_called_once()
    conn.close.assert_called_once()
    assert any("status = 'Running'" in str(c.args[0]) for c in cur.execute.call_args_list)


def test_workflow_submission_failure_marks_campaign_failed(monkeypatch):
    conn, cur, trigger = _wire(monkeypatch, [dict(APPROVED), {"segment_tag": "vip"}])
    trigger.side_effect = RuntimeError("Dagster unavailable")

    with pytest.raises(RuntimeError, match="Dagster unavailable"):
        activate_campaign("c1", "t1", log=lambda *_: None)

    assert any("status = 'Failed'" in str(c.args[0]) for c in cur.execute.call_args_list)


@pytest.mark.parametrize(
    "rows, err",
    [
        ([None], "not found"),
        ([{**APPROVED, "approval_status": "Draft"}], "is not Approved"),
        ([{**APPROVED, "workflow_id": None}], "no pinned workflow"),
        ([{**APPROVED, "workflow_approval_status": "InReview"}], "is not Approved"),
        ([{**APPROVED, "segment_id": None}], "no segment_id"),
        ([{**APPROVED, "status": "Running"}], "already Running"),
        ([{**APPROVED, "status": "Completed"}], "explicit restart is required"),
        ([dict(APPROVED), None], "not found or inactive"),
        ([dict(APPROVED), {"segment_tag": None}], "not found or inactive"),
    ],
)
def test_validation_failures_raise_and_never_submit(monkeypatch, rows, err):
    conn, _cur, trigger = _wire(monkeypatch, rows)

    with pytest.raises(CampaignActivationError, match=err):
        activate_campaign("c1", "t1", log=lambda *_: None)

    trigger.assert_not_called()
    conn.commit.assert_not_called()
    conn.close.assert_called_once()


def test_completed_restart_allocates_new_execution_id(monkeypatch):
    completed = {**APPROVED, "status": "Completed"}
    conn, cur, trigger = _wire(monkeypatch, [completed, {"segment_tag": "vip"}])

    result = activate_campaign(
        "c1", "t1", restart=True,
        expected_execution_id=completed["workflow_execution_id"], log=lambda *_: None,
    )

    assert result["status"] == "Running"
    assert result["execution_id"] != completed["workflow_execution_id"]
    trigger.assert_called_once()
    assert trigger.call_args.kwargs["execution_id"] == result["execution_id"]
    update = next(
        call for call in cur.execute.call_args_list
        if "workflow_execution_id = %(execution_id)s" in str(call.args[0])
    )
    assert update.args[1]["execution_id"] == result["execution_id"]
    conn.commit.assert_called_once()


def test_failed_campaign_retry_reuses_execution_id(monkeypatch):
    failed = {**APPROVED, "status": "Failed"}
    _conn, _cur, trigger = _wire(monkeypatch, [failed, {"segment_tag": "vip"}])

    result = activate_campaign("c1", "t1", log=lambda *_: None)

    assert result["execution_id"] == failed["workflow_execution_id"]
    assert trigger.call_args.kwargs["execution_id"] == failed["workflow_execution_id"]


def test_restart_flag_is_refused_before_completion(monkeypatch):
    conn, _cur, trigger = _wire(monkeypatch, [dict(APPROVED)])
    with pytest.raises(CampaignActivationError, match="only be restarted after it is Completed"):
        activate_campaign("c1", "t1", restart=True, log=lambda *_: None)
    trigger.assert_not_called()
    conn.commit.assert_not_called()


def test_queued_restart_cannot_replay_an_execution_that_already_changed(monkeypatch):
    completed = {**APPROVED, "status": "Completed"}
    conn, _cur, trigger = _wire(monkeypatch, [completed])
    with pytest.raises(CampaignActivationError, match="changed"):
        activate_campaign(
            "c1", "t1", restart=True,
            expected_execution_id="a56cf6de-9e48-4f1d-ae2e-58558b220d14",
            log=lambda *_: None,
        )
    trigger.assert_not_called()
    conn.commit.assert_not_called()
