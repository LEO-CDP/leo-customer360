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


def test_email_env_smtp_fallback_uses_mailpit_settings(monkeypatch):
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    for key, value in {
        "EMAIL_DISPATCH_ADAPTER": "smtp",
        "SMTP_HOST": "127.0.0.1",
        "SMTP_PORT": "1025",
        "SMTP_USERNAME": "",
        "SMTP_PASSWORD": "",
        "SMTP_USE_TLS": "false",
        "EMAIL_FROM_ADDRESS": "uat@example.test",
        "EMAIL_FROM_NAME": "Mailpit UAT",
    }.items():
        monkeypatch.setenv(key, value)

    connector = workflow_runner._load_email_connector(cursor, "tenant-1")

    assert connector is not None
    assert connector.provider == "smtp"
    assert connector.config == {
        "smtp_host": "127.0.0.1",
        "smtp_port": 1025,
        "smtp_username": None,
        "smtp_use_tls": False,
        "from_address": "uat@example.test",
        "from_name": "Mailpit UAT",
    }
    assert connector.credentials == {"password": None}

    server = MagicMock()
    smtp = MagicMock()
    smtp.return_value.__enter__.return_value = server
    monkeypatch.setattr(workflow_runner.smtplib, "SMTP", smtp)
    gateway = EmailWorkflowGateway(FakeLedger(), connector)

    assert gateway.send(_message()).status == "sent"
    smtp.assert_called_once_with("127.0.0.1", 1025, timeout=30)
    server.starttls.assert_not_called()
    server.login.assert_not_called()
    server.send_message.assert_called_once()


def test_active_tenant_email_connector_takes_precedence_over_env(monkeypatch):
    cursor = MagicMock()
    cursor.fetchone.return_value = {
        "provider": "smtp",
        "config": {"smtp_host": "tenant-smtp.example", "smtp_port": 2525},
        "credentials": {"password": "tenant-secret"},
    }
    monkeypatch.setenv("EMAIL_DISPATCH_ADAPTER", "smtp")
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", "1025")

    connector = workflow_runner._load_email_connector(cursor, "tenant-1")

    assert connector is not None
    assert connector.config["smtp_host"] == "tenant-smtp.example"
    assert connector.config["smtp_port"] == 2525
    assert connector.credentials["password"] == "tenant-secret"


def test_missing_tenant_email_connector_without_env_fallback_remains_unconfigured(monkeypatch):
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    monkeypatch.delenv("EMAIL_DISPATCH_ADAPTER", raising=False)

    assert workflow_runner._load_email_connector(cursor, "tenant-1") is None


@pytest.mark.parametrize(
    "environment",
    [
        {"EMAIL_DISPATCH_ADAPTER": "unsupported"},
        {"EMAIL_DISPATCH_ADAPTER": "smtp", "SMTP_HOST": ""},
        {"EMAIL_DISPATCH_ADAPTER": "smtp", "SMTP_HOST": "127.0.0.1", "SMTP_PORT": "not-a-port"},
        {"EMAIL_DISPATCH_ADAPTER": "smtp", "SMTP_HOST": "127.0.0.1", "SMTP_PORT": "1025", "SMTP_USE_TLS": "sometimes"},
    ],
)
def test_email_env_fallback_rejects_invalid_smtp_configuration(monkeypatch, environment):
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    monkeypatch.delenv("EMAIL_DISPATCH_ADAPTER", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_PORT", raising=False)
    monkeypatch.delenv("SMTP_USE_TLS", raising=False)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(workflow_runner.CampaignWorkflowError):
        workflow_runner._load_email_connector(cursor, "tenant-1")


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
    monkeypatch.delenv("EMAIL_DISPATCH_ADAPTER", raising=False)
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


def test_sync_submission_submits_and_handles_graphql_error(monkeypatch):
    fake_client = MagicMock()
    fake_client.submit_job_execution.return_value = "sync-run-42"
    monkeypatch.setattr(workflow_runner, "DagsterGraphQLClient", MagicMock(return_value=fake_client))
    workflow = {"root": {
        "id": "sync", "type": "run_data_synch_job",
        "config": {"platform": "meta_ads", "audience_name": "VIP Audience"},
    }}

    runs = workflow_runner._submit_audience_sync_jobs(
        tenant_id="t1", campaign_id="c1", segment_id="s1",
        workflow=workflow, reached_node_ids={"sync"},
        execution_id="exec-1", log=lambda *_: None,
    )
    assert runs == ["sync-run-42"]
    kwargs = fake_client.submit_job_execution.call_args.kwargs
    assert kwargs["run_config"]["ops"]["run_data_synch_op"]["config"]["platform"] == "meta_ads"
    assert kwargs["tags"]["execution_id"] == "exec-1"

    fake_client.submit_job_execution.side_effect = RuntimeError("Dagster down")
    with pytest.raises(workflow_runner.CampaignWorkflowError, match="could not submit audience-sync action"):
        workflow_runner._submit_audience_sync_jobs(
            tenant_id="t1", campaign_id="c1", segment_id="s1",
            workflow=workflow, reached_node_ids={"sync"},
            execution_id="exec-1", log=lambda *_: None,
        )


def test_ledger_begin_unlocks_and_rolls_back_when_query_raises():
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.execute.side_effect = [
        None,  # set_tenant_context
        None,  # pg_try_advisory_lock
        RuntimeError("db error during select"),
        None,  # pg_advisory_unlock
    ]
    cursor.fetchone.return_value = {"acquired": True}
    ledger = workflow_runner.WorkflowLedger(
        connection, "tenant", "campaign", "run-1", uuid.uuid4()
    )

    with pytest.raises(RuntimeError, match="db error during select"):
        ledger.begin(uuid.uuid4(), "node-1", "email", "email")

    connection.rollback.assert_called_once()
    assert any("pg_advisory_unlock" in str(c.args[0]) for c in cursor.execute.call_args_list)


def test_ledger_finish_always_unlocks_on_update_error():
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.execute.side_effect = [
        None,  # set_tenant_context
        RuntimeError("db error during update"),
        None,  # pg_advisory_unlock
    ]
    ledger = workflow_runner.WorkflowLedger(
        connection, "tenant", "campaign", "run-1", uuid.uuid4()
    )

    with pytest.raises(RuntimeError, match="db error during update"):
        ledger.finish(uuid.uuid4(), "node-1", "Sent", provider="smtp")

    connection.rollback.assert_called_once()
    assert any("pg_advisory_unlock" in str(c.args[0]) for c in cursor.execute.call_args_list)


def test_ledger_begin_updates_existing_failed_row():
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [{"acquired": True}, {"status": "Failed"}]
    ledger = workflow_runner.WorkflowLedger(
        connection, "tenant", "campaign", "run-2", uuid.uuid4()
    )

    assert ledger.begin(uuid.uuid4(), "node-1", "email", "email") is True
    updates = [
        c for c in cursor.execute.call_args_list
        if "SET status = 'Running', attempt_count = attempt_count + 1" in str(c.args[0])
    ]
    assert len(updates) == 1


def test_webhook_and_onesignal_push_gateway_variants(monkeypatch):
    ledger = FakeLedger()
    msg = _message("push")
    tokens = {str(msg.profile_id): ["sub-token-1"]}

    # Missing OneSignal credentials raises CampaignWorkflowError (recorded as failed in ledger)
    bad_os = WebhookWorkflowGateway(
        ledger, "push", Connector("onesignal", {"app_id": "app-1"}, {}), tokens
    )
    receipt = bad_os.send(msg)
    assert receipt.status == "failed"
    assert "missing app_id or api_key" in ledger.finished[-1][3]["error"]

    # Missing push token skips without network
    no_tok = WebhookWorkflowGateway(
        ledger, "push", Connector("onesignal", {"app_id": "app-1"}, {"api_key": "k"}), {}
    )
    receipt = no_tok.send(msg)
    assert receipt.status == "skipped"
    assert receipt.detail == "no push token"
    assert ledger.finished[-1][3]["metadata"] == {"skip_reason": "no push token"}

    # Valid OneSignal push call
    fake_resp = MagicMock()
    fake_resp.__enter__.return_value.read.return_value = b'{"id":"os-123"}'
    monkeypatch.setattr(workflow_runner, "urlopen", MagicMock(return_value=fake_resp))
    good_os = WebhookWorkflowGateway(
        ledger, "push", Connector("onesignal", {"app_id": "app-1"}, {"api_key": "k"}), tokens
    )
    receipt = good_os.send(msg)
    assert receipt.status == "sent"
    assert receipt.provider_id.startswith("onesignal-")

    # Webhook push without webhook_url fails
    bad_wh = WebhookWorkflowGateway(ledger, "push", Connector("webhook", {}, {}), tokens)
    assert bad_wh.send(msg).status == "failed"

    # Webhook push with webhook_url succeeds
    good_wh = WebhookWorkflowGateway(
        ledger, "push", Connector("webhook", {"webhook_url": "https://example.test/push"}, {}), tokens
    )
    assert good_wh.send(msg).status == "sent"


def test_notification_gateway_mobile_native_and_webhook_variants(monkeypatch):
    ledger = FakeLedger()
    msg = _message("notification").model_copy(update={"provider": "mobile_native"})
    tokens = {str(msg.profile_id): ["mob-token-1"]}

    # Missing mobile token skips
    gw_no_tok = NotificationWorkflowGateway(
        ledger,
        {"mobile_native": Connector("onesignal", {"app_id": "a"}, {"api_key": "k"})},
        {},
    )
    receipt = gw_no_tok.send(msg)
    assert receipt.status == "skipped"
    assert receipt.detail == "no mobile token"

    # Missing mobile_native app_id/api_key fails cleanly
    gw_bad_cfg = NotificationWorkflowGateway(
        ledger,
        {"mobile_native": Connector("onesignal", {}, {})},
        tokens,
    )
    receipt = gw_bad_cfg.send(msg)
    assert receipt.status == "failed"
    assert "missing app_id or api_key" in ledger.finished[-1][3]["error"]

    # Valid mobile_native HTTP call and valid webhook SMS call
    fake_resp = MagicMock()
    fake_resp.__enter__.return_value.read.return_value = b'{"ok":true}'
    monkeypatch.setattr(workflow_runner, "urlopen", MagicMock(return_value=fake_resp))
    gw_valid = NotificationWorkflowGateway(
        ledger,
        {
            "mobile_native": Connector("onesignal", {"app_id": "a"}, {"api_key": "k"}),
            "sms": Connector("webhook", {"webhook_url": "https://example.test/sms"}, {}),
            "whatsapp": Connector("webhook", {}, {}),
        },
        tokens,
    )
    assert gw_valid.send(msg).status == "sent"
    assert gw_valid.send(_message("notification").model_copy(update={"provider": "sms"})).status == "sent"
    assert gw_valid.send(_message("notification").model_copy(update={"provider": "whatsapp"})).status == "failed"


def test_workflow_profile_writer_records_ledger_for_add_remove_and_error():
    connection = MagicMock()
    ledger = FakeLedger()
    writer = workflow_runner.WorkflowProfileWriter(connection, "tenant-1", ledger=ledger)
    profile = workflow_runner._profile_from_row({
        "master_profile_id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
    })

    writer.current_node_id = "tag-add"
    writer.apply(profile, "add_tag", "vip")
    assert ledger.started[-1] == (profile.master_profile_id, "tag-add", "update_profile", "profile")
    assert ledger.finished[-1][2] == "Applied"
    assert ledger.finished[-1][3]["metadata"] == {"action": "add_tag", "tag": "vip"}

    writer.current_node_id = "tag-rm"
    writer.apply(profile, "remove_tag", "vip")
    assert ledger.finished[-1][2] == "Applied"
    assert ledger.finished[-1][3]["metadata"] == {"action": "remove_tag", "tag": "vip"}

    with pytest.raises(workflow_runner.CampaignWorkflowError, match="Unsupported profile action"):
        writer.apply(profile, "invalid_action", "vip")
    assert ledger.finished[-1][2] == "Failed"


def test_iter_profile_rows_paginates_until_short_batch():
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    id1, id2, id3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    cursor.fetchall.side_effect = [
        [{"master_profile_id": id1}, {"master_profile_id": id2}],
        [{"master_profile_id": id3}],
    ]

    rows = list(workflow_runner._iter_profile_rows(connection, "tenant-1", "vip", batch_size=2))
    assert [r["master_profile_id"] for r in rows] == [id1, id2, id3]
    assert cursor.execute.call_count == 4  # 2 batches * (set_tenant_context + SELECT)


def test_profile_query_selects_all_condition_score_fields():
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = []

    assert list(workflow_runner._iter_profile_rows(connection, "tenant-1", "vip", 10)) == []

    select_sql = next(
        call.args[0]
        for call in cursor.execute.call_args_list
        if "SELECT p.master_profile_id" in str(call.args[0])
    )
    for field in (
        "engagement_score",
        "churn_probability",
        "lead_conversion_probability",
        "historical_clv",
        "predictive_clv",
        "latest_nps_score",
        "average_csat",
        "overall_sentiment_score",
        "profile_completeness_score",
        "identity_confidence_score",
        "segmentation_tags",
    ):
        assert f"p.{field}" in select_sql


@pytest.mark.parametrize(
    "override, match",
    [
        ({"approval_status": "InReview"}, "Campaign must be Approved"),
        ({"workflow_approval_status": "Rejected"}, "Pinned workflow version must be Approved"),
        ({"workflow": None}, "no persisted workflow"),
        (
            {
                "workflow": {
                    "schema_version": 2,
                    "root": {
                        "id": "trigger",
                        "type": "trigger",
                        "config": {"event": "signup", "segment_id": str(uuid.uuid4())},
                        "children": [],
                    },
                }
            },
            "Workflow audience must match",
        ),
        (
            {
                "workflow": {
                    "schema_version": 2,
                    "root": {
                        "id": "trigger",
                        "type": "trigger",
                        "config": {"event": "unknown_event", "segment_id": " MATCH "},
                        "children": [],
                    },
                }
            },
            "Workflow is invalid",
        ),
    ],
)
def test_runner_validates_loaded_campaign_and_marks_failed(monkeypatch, override, match):
    action = {"id": "email", "type": "email", "config": {"subject": "Hi", "body": "Hello"}}
    connection, tenant_id, campaign_id, _, status = _workflow_run(monkeypatch, action, [])
    cursor = connection.cursor.return_value.__enter__.return_value
    row = dict(cursor.fetchone.return_value)
    if override.get("workflow") and override["workflow"]["root"]["config"].get("segment_id") == " MATCH ":
        override = {
            "workflow": {
                **override["workflow"],
                "root": {
                    **override["workflow"]["root"],
                    "config": {"event": "unknown_event", "segment_id": row["segment_id"]},
                },
            }
        }
    row.update(override)
    cursor.fetchone.return_value = row

    with pytest.raises(workflow_runner.CampaignWorkflowError, match=match):
        workflow_runner.run_campaign_workflow(tenant_id=tenant_id, campaign_id=campaign_id)

    status.assert_called_once()
    assert status.call_args.args[3] == "Failed"


def test_runner_returns_observability_telemetry_in_summary(monkeypatch):
    action = {
        "id": "cond",
        "type": "condition",
        "config": {"field": "engagement_score", "op": "ge", "value": 50},
        "children": [
            {
                "id": "tag-vip",
                "type": "update_profile",
                "branch": "yes",
                "config": {"action": "add_tag", "tag": "engaged"},
                "children": [
                    {"id": "goal-1", "type": "exit", "config": {"kind": "goal"}},
                ],
            },
            {
                "id": "exit-1",
                "type": "exit",
                "branch": "no",
                "config": {"kind": "exit"},
            },
        ],
    }
    profiles = [
        {"master_profile_id": uuid.uuid4(), "engagement_score": 80},
        {"master_profile_id": uuid.uuid4(), "engagement_score": 20},
    ]
    _, tenant_id, campaign_id, _, _ = _workflow_run(monkeypatch, action, profiles)
    ledger = FakeLedger()
    monkeypatch.setattr(workflow_runner, "WorkflowLedger", lambda *_: ledger)

    summary = workflow_runner.run_campaign_workflow(
        tenant_id=tenant_id, campaign_id=campaign_id, run_id="dagster-run-99"
    )

    assert summary["status"] == "Completed"
    assert summary["run_id"] == "dagster-run-99"
    assert summary["profiles"] == 2
    assert summary["applied"] == 1
    assert summary["end_counts"] == {"goal": 1, "exit": 1, "ended": 0, "error": 0}
    assert summary["node_counts"] == {
        "trigger": 2,
        "cond": 2,
        "tag-vip": 1,
        "goal-1": 1,
        "exit-1": 1,
    }
    assert summary["started_at"] is not None
    assert summary["finished_at"] is not None
    assert summary["duration_seconds"] >= 0.0
    assert any(item[1] == "tag-vip" and item[2] == "update_profile" for item in ledger.started)
