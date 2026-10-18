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
    "workflow_approval_status": "Approved",
}


def _wire(monkeypatch, fetchones):
    cur = MagicMock()
    cur.fetchone.side_effect = list(fetchones)
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    trigger = MagicMock(return_value="workflow-run-1")
    monkeypatch.setattr(activation, "connect", lambda: conn)
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
    }
    trigger.assert_called_once_with("c1", "t1", log=trigger.call_args.kwargs["log"])
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
