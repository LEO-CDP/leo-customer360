import uuid
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from campaign_activation import workflow_runner
from campaign_activation.workflow_runner import (
    Connector,
    EmailWorkflowGateway,
    NotificationWorkflowGateway,
    WebhookWorkflowGateway,
)
from leo_customer360_dao.workflow.models import OutboundMessage


class FakeLedger:
    def __init__(self):
        self.started = []
        self.finished = []

    def begin(self, profile_id, node_id, action_type, channel):
        self.started.append((profile_id, node_id, action_type, channel))
        return True

    def finish(self, profile_id, node_id, status, **kwargs):
        self.finished.append((profile_id, node_id, status, kwargs))


def test_action_ledger_deduplicates_within_execution_but_allows_full_replay():
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [
        {"acquired": True}, {"status": "Sent"},
        {"acquired": True}, None,
    ]
    profile_id = uuid.uuid4()
    previous_execution, replay_execution = uuid.uuid4(), uuid.uuid4()
    previous = workflow_runner.WorkflowLedger(
        connection, "tenant", "campaign", "dagster-old", previous_execution,
    )
    replay = workflow_runner.WorkflowLedger(
        connection, "tenant", "campaign", "dagster-replay", replay_execution,
    )

    assert previous.begin(profile_id, "email-1", "email", "email") is False
    assert replay.begin(profile_id, "email-1", "email", "email") is True

    lookups = [
        call for call in cursor.execute.call_args_list
        if "SELECT status" in str(call.args[0])
    ]
    inserts = [
        call for call in cursor.execute.call_args_list
        if "INSERT INTO customer360.crm_campaign_workflow_action_runs" in str(call.args[0])
    ]
    assert [call.args[1]["execution_id"] for call in lookups] == [
        str(previous_execution), str(replay_execution),
    ]
    assert len(inserts) == 1
    assert inserts[0].args[1]["execution_id"] == str(replay_execution)


def test_busy_action_ledger_fails_instead_of_allowing_downstream_actions():
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = {"acquired": False}
    ledger = workflow_runner.WorkflowLedger(
        connection, "tenant", "campaign", "dagster-run", uuid.uuid4(),
    )
    with pytest.raises(workflow_runner.CampaignWorkflowError, match="already executing"):
        ledger.begin(uuid.uuid4(), "email-1", "email", "email")


def _message(channel="email"):
    return OutboundMessage(
        channel=channel,
        profile_id=uuid.uuid4(),
        to="person@example.com",
        node_id="n2",
        at_hours=0,
        subject="Hello",
        body="Welcome",
    )


def test_email_workflow_gateway_mock_records_success():
    ledger = FakeLedger()
    gateway = EmailWorkflowGateway(ledger, Connector("mock", {}, {}))

    result = gateway.send(_message())

    assert result.status == "sent"
    assert result.provider_id.startswith("workflow-email-mock-")
    assert ledger.started[0][1:] == ("n2", "email", "email")
    assert ledger.finished[0][2] == "Sent"


def test_sms_workflow_gateway_mock_records_success_without_network():
    ledger = FakeLedger()
    gateway = WebhookWorkflowGateway(ledger, "notification", Connector("mock", {}, {}), {})

    result = gateway.send(_message("notification"))

    assert result.status == "sent"
    assert result.provider_id.startswith("workflow-notification-mock-")
    assert ledger.finished[0][2] == "Sent"


def test_notification_gateway_preserves_provider_in_ledger():
    ledger = FakeLedger()
    gateway = NotificationWorkflowGateway(
        ledger,
        {"zalo_oa": Connector("mock", {}, {})},
        {},
    )
    message = _message("notification").model_copy(update={"provider": "zalo_oa"})

    result = gateway.send(message)

    assert result.status == "sent"
    assert result.provider_id.startswith("workflow-zalo_oa-mock-")
    assert ledger.started[0][2:] == ("notification", "notification")


@pytest.mark.parametrize("channel", ["email", "notification", "push"])
def test_missing_connector_logs_skip_without_counting_a_send(channel, caplog):
    ledger = FakeLedger()
    if channel == "email":
        gateway = EmailWorkflowGateway(ledger, None)
    elif channel == "notification":
        gateway = NotificationWorkflowGateway(ledger, {}, {})
    else:
        gateway = WebhookWorkflowGateway(ledger, channel, None, {})

    with caplog.at_level(logging.INFO):
        receipt = gateway.send(_message(channel))

    assert receipt.status == "skipped"
    assert receipt.provider_id is None
    assert "not configured" in receipt.detail
    assert "not configured" in caplog.text
    assert "person@example.com" not in caplog.text
    assert ledger.finished[0][2] == "Skipped"


def test_smtp_uses_password_saved_by_provider_api(monkeypatch):
    server = MagicMock()
    smtp = MagicMock()
    smtp.return_value.__enter__.return_value = server
    monkeypatch.setattr(workflow_runner.smtplib, "SMTP", smtp)
    gateway = EmailWorkflowGateway(
        FakeLedger(),
        Connector("smtp", {"smtp_username": "mailer"}, {"password": "test-password"}),
    )

    assert gateway.send(_message()).status == "sent"
    server.login.assert_called_once_with("mailer", "test-password")


def test_database_event_source_uses_dictionary_cursor():
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    started_at = datetime.now(timezone.utc)
    cursor.fetchone.return_value = {"event_time": started_at + timedelta(hours=2)}
    source = workflow_runner.DatabaseEventSource(connection, str(uuid.uuid4()), started_at)

    assert source.occurs(MagicMock(master_profile_id=uuid.uuid4()), "purchase", 0, 3) == 2
    connection.cursor.assert_called_once_with(cursor_factory=workflow_runner.RealDictCursor)


def _workflow_run(monkeypatch, action, profiles):
    connection = MagicMock()
    tenant_id, campaign_id, segment_id = (str(uuid.uuid4()) for _ in range(3))
    workflow = {
        "schema_version": 2,
        "root": {
            "id": "trigger",
            "type": "trigger",
            "config": {"event": "signup", "segment_id": segment_id},
            "children": [action],
        },
    }
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = {
        "approval_status": "Approved",
        "workflow_approval_status": "Approved",
        "segment_id": segment_id,
        "segment_tag": "vip",
        "workflow": workflow,
    }
    monkeypatch.setattr(workflow_runner, "connect", lambda: connection)
    monkeypatch.setattr(workflow_runner, "lock_campaign_execution", lambda *_, **__: True)
    monkeypatch.setattr(workflow_runner, "_load_connector", lambda *_: None)
    monkeypatch.setattr(workflow_runner, "_iter_profile_rows", lambda *_: iter(profiles))
    submit = MagicMock(return_value=[])
    monkeypatch.setattr(workflow_runner, "_submit_audience_sync_jobs", submit)
    status = MagicMock()
    monkeypatch.setattr(workflow_runner, "_set_campaign_status", status)
    return connection, tenant_id, campaign_id, submit, status


def test_runner_missing_providers_completes_without_fake_sends(monkeypatch, caplog):
    action = {"id": "email", "type": "email", "config": {"subject": "Hi", "body": "Hello"}}
    profiles = [{
        "master_profile_id": uuid.uuid4(),
        "email": "private@example.com",
        "communication_preferences": {"email_opt_in": True},
    }]
    connection, tenant_id, campaign_id, submit, status = _workflow_run(monkeypatch, action, profiles)
    monkeypatch.setattr(workflow_runner, "WorkflowLedger", lambda *_: FakeLedger())

    with caplog.at_level(logging.INFO):
        result = workflow_runner.run_campaign_workflow(tenant_id=tenant_id, campaign_id=campaign_id)

    assert result["profiles"] == 1
    assert result["sent"] == result["errors"] == 0
    assert result["skipped"] == 1
    assert "private@example.com" not in caplog.text
    status.assert_called_once_with(
        connection, tenant_id, campaign_id, "Completed", execution_id=result["execution_id"],
    )
    assert submit.call_args.kwargs["reached_node_ids"] == set()


def test_runner_records_provider_failure_and_continues_next_profile(monkeypatch):
    action = {"id": "email", "type": "email", "config": {"subject": "Hi", "body": "Hello"}}
    profiles = [{
        "master_profile_id": uuid.uuid4(),
        "email": "private@example.com",
        "communication_preferences": {"email_opt_in": True},
    } for _ in range(2)]
    connection, tenant_id, campaign_id, _, status = _workflow_run(monkeypatch, action, profiles)
    ledger = FakeLedger()
    monkeypatch.setattr(workflow_runner, "WorkflowLedger", lambda *_: ledger)
    monkeypatch.setattr(
        workflow_runner.EmailWorkflowGateway, "_send",
        MagicMock(side_effect=RuntimeError("provider unavailable")),
    )

    result = workflow_runner.run_campaign_workflow(tenant_id=tenant_id, campaign_id=campaign_id)

    assert result["profiles"] == result["errors"] == 2
    assert result["sent"] == 0
    assert connection.rollback.call_count == 2
    assert [entry[2] for entry in ledger.finished] == ["Failed", "Failed"]
    status.assert_called_once_with(
        connection, tenant_id, campaign_id, "Failed", execution_id=result["execution_id"],
    )


def test_sync_submits_only_reached_nodes(monkeypatch):
    action = {
        "id": "condition", "type": "condition",
        "config": {"field": "engagement_score", "op": "ge", "value": 60},
        "children": [
            {"id": "exit", "type": "exit", "branch": "yes", "config": {"kind": "goal"}},
            {"id": "sync", "type": "run_data_synch_job", "branch": "no",
             "config": {"platform": "meta_ads", "audience_name": "Low engagement"}},
        ],
    }
    profiles = [{"master_profile_id": uuid.uuid4(), "engagement_score": 80}]
    _, tenant_id, campaign_id, submit, _ = _workflow_run(monkeypatch, action, profiles)

    workflow_runner.run_campaign_workflow(tenant_id=tenant_id, campaign_id=campaign_id)

    assert submit.call_args.kwargs["reached_node_ids"] == set()


@pytest.mark.parametrize("batch_size", [0, -1, 100001])
def test_runner_rejects_invalid_batch_size_before_connecting(monkeypatch, batch_size):
    connect = MagicMock()
    monkeypatch.setattr(workflow_runner, "connect", connect)
    with pytest.raises(ValueError, match="batch_size"):
        workflow_runner.run_campaign_workflow(
            tenant_id=str(uuid.uuid4()), campaign_id=str(uuid.uuid4()), batch_size=batch_size,
        )
    connect.assert_not_called()


def test_missing_connector_is_not_synthesized_as_mock():
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    assert workflow_runner._load_connector(cursor, "tenant", "EMAIL") is None


def test_provider_exception_does_not_log_recipient_details(monkeypatch, caplog):
    ledger = FakeLedger()
    gateway = EmailWorkflowGateway(ledger, Connector("smtp", {}, {}))
    monkeypatch.setattr(gateway, "_send", MagicMock(
        side_effect=RuntimeError("SMTP rejected person@example.com"),
    ))

    receipt = gateway.send(_message())

    assert receipt.status == "failed"
    assert "RuntimeError" in caplog.text
    assert "person@example.com" not in caplog.text
    assert "person@example.com" not in receipt.detail
    assert ledger.finished[0][3]["error"] == "SMTP rejected person@example.com"


def test_missing_or_completed_campaign_is_not_marked_failed(monkeypatch):
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchone.return_value = None
    monkeypatch.setattr(workflow_runner, "connect", lambda: connection)
    monkeypatch.setattr(workflow_runner, "lock_campaign_execution", lambda *_, **__: True)
    status = MagicMock()
    monkeypatch.setattr(workflow_runner, "_set_campaign_status", status)

    with pytest.raises(workflow_runner.CampaignWorkflowError, match="not found"):
        workflow_runner.run_campaign_workflow(
            tenant_id=str(uuid.uuid4()), campaign_id=str(uuid.uuid4()),
        )

    status.assert_not_called()
    connection.rollback.assert_called_once()
    connection.close.assert_called_once()


def test_stale_execution_cannot_mark_newer_campaign_execution_failed(monkeypatch):
    action = {"id": "email", "type": "email", "config": {"subject": "Hi", "body": "Hello"}}
    connection, tenant_id, campaign_id, _, status = _workflow_run(monkeypatch, action, [])
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value["workflow_execution_id"] = uuid.uuid4()

    with pytest.raises(workflow_runner.CampaignWorkflowError, match="execution ID is stale"):
        workflow_runner.run_campaign_workflow(
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            execution_id=str(uuid.uuid4()),
        )

    status.assert_not_called()
    connection.close.assert_called_once()


def test_legacy_execution_id_is_persisted_before_profile_actions(monkeypatch):
    action = {"id": "email", "type": "email", "config": {"subject": "Hi", "body": "Hello"}}
    connection, tenant_id, campaign_id, _, _ = _workflow_run(monkeypatch, action, [])
    result = workflow_runner.run_campaign_workflow(tenant_id=tenant_id, campaign_id=campaign_id)
    cursor = connection.cursor.return_value.__enter__.return_value
    writes = [
        call for call in cursor.execute.call_args_list
        if "SET workflow_execution_id" in str(call.args[0])
    ]
    assert len(writes) == 1
    assert writes[0].args[1]["execution_id"] == result["execution_id"]


def test_sync_targets_are_reachable_and_deduplicated():
    workflow = {"root": {
        "id": "sync-1", "type": "run_data_synch_job",
        "config": {"platform": "meta_ads", "audience_name": "UAT"},
        "children": [{
            "id": "sync-2", "type": "run_data_synch_job",
            "config": {"platform": "meta_ads", "audience_name": "UAT"},
        }, {
            "id": "not-reached", "type": "run_data_synch_job",
            "config": {"platform": "google_ads", "audience_name": "Other"},
        }],
    }}
    assert workflow_runner._workflow_sync_targets(workflow, {"sync-1", "sync-2"}) == [
        {"platform": "meta_ads", "audience_name": "UAT"},
    ]


def test_sync_submission_does_not_contact_dagster_for_unvisited_nodes(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(workflow_runner, "DagsterGraphQLClient", client)
    assert workflow_runner._submit_audience_sync_jobs(
        tenant_id="tenant", campaign_id="campaign", segment_id="segment",
        workflow={"root": {
            "id": "sync", "type": "run_data_synch_job",
            "config": {"platform": "meta_ads", "audience_name": "UAT"},
        }},
        reached_node_ids=set(), log=lambda *_: None,
        execution_id=str(uuid.uuid4()),
    ) == []
    client.assert_not_called()
