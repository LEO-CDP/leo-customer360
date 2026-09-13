"""Unit tests for activate_campaign: the validation gates and the happy-path
handoff (no DB, no Dagster; connect() and the email-engine trigger are mocked)."""

import sys
import types
from unittest.mock import MagicMock

import pytest

# triggers imports dagster_graphql at module top; stub it when absent so this
# suite runs without the Dagster dep (the client is mocked away anyway).
if "dagster_graphql" not in sys.modules:
    try:
        import dagster_graphql  # noqa: F401
    except ModuleNotFoundError:
        stub = types.ModuleType("dagster_graphql")
        stub.DagsterGraphQLClient = object
        sys.modules["dagster_graphql"] = stub

from campaign_activation import activation
from campaign_activation.activation import CampaignActivationError, activate_campaign

APPROVED = {
    "campaign_id": "c1", "name": "N", "approval_status": "Approved",
    "status": "Draft", "segment_id": "s1", "template_id": "t1",
}


def _wire(monkeypatch, fetchones):
    """Point connect() at a fake conn whose cursor yields `fetchones` in order."""
    cur = MagicMock()
    cur.fetchone.side_effect = list(fetchones)
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    trigger = MagicMock(return_value="email-run-1")
    monkeypatch.setattr(activation, "connect", lambda: conn)
    monkeypatch.setattr(activation, "trigger_email_engine_job", trigger)
    return conn, cur, trigger


def test_happy_path_marks_running_and_triggers(monkeypatch):
    conn, cur, trigger = _wire(monkeypatch, [
        dict(APPROVED), {"status": "Approved"}, {"segment_tag": "vip"}, {"n": 5},
    ])
    out = activate_campaign("c1", "t1", log=lambda *_: None)

    assert out == {"campaign_id": "c1", "tenant_id": "t1",
                   "snapshot_count": 5, "email_engine_run_id": "email-run-1"}
    assert trigger.call_args.args == ("c1", "t1")
    conn.commit.assert_called_once()
    conn.close.assert_called_once()
    assert any("Running" in str(c.args[0]) for c in cur.execute.call_args_list)


def test_no_trigger_when_disabled(monkeypatch):
    conn, cur, trigger = _wire(monkeypatch, [
        dict(APPROVED), {"status": "Approved"}, {"segment_tag": "vip"}, {"n": 0},
    ])
    out = activate_campaign("c1", "t1", trigger_email=False, log=lambda *_: None)

    assert out["email_engine_run_id"] is None
    assert out["snapshot_count"] == 0
    trigger.assert_not_called()


@pytest.mark.parametrize("rows, err", [
    ([None], "not found"),
    ([{**APPROVED, "approval_status": "Draft"}], "is not Approved"),
    ([{**APPROVED, "template_id": None}], "no template_id"),
    ([{**APPROVED, "segment_id": None}], "no segment_id"),
    ([dict(APPROVED), {"status": "Draft"}], "template t1 is not Approved"),
    ([dict(APPROVED), {"status": "Approved"}, None], "has no segment_tag"),
    ([dict(APPROVED), {"status": "Approved"}, {"segment_tag": None}], "has no segment_tag"),
])
def test_validation_failures_raise_and_dont_trigger(monkeypatch, rows, err):
    conn, _cur, trigger = _wire(monkeypatch, rows)
    with pytest.raises(CampaignActivationError) as exc:
        activate_campaign("c1", "t1", log=lambda *_: None)

    assert err in str(exc.value)
    trigger.assert_not_called()
    conn.commit.assert_not_called()
    conn.close.assert_called_once()  # finally: connection always released
