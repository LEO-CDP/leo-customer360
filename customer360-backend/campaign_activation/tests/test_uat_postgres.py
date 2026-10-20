"""Opt-in PostgreSQL checks; all DDL and fixture data are rolled back."""

import os
import json
import importlib
from pathlib import Path
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from leo_customer360_dao.config import settings

from campaign_activation.rls import lock_campaign_execution
from campaign_activation.workflow_runner import WorkflowLedger
from campaign_activation import activation, workflow_runner

pytestmark = pytest.mark.skipif(
    os.environ.get("CAMPAIGN_PG_UAT") != "1",
    reason="Set CAMPAIGN_PG_UAT=1 for rollback-only PostgreSQL verification",
)


class TransactionalConnection:
    """Keep service commits inside a test-owned outer transaction."""

    def __init__(self, raw):
        self.raw = raw
        with raw.cursor() as cursor:
            cursor.execute("SAVEPOINT service_transaction")

    def cursor(self, **kwargs):
        return self.raw.cursor(**kwargs)

    def commit(self):
        with self.raw.cursor() as cursor:
            cursor.execute("RELEASE SAVEPOINT service_transaction")
            cursor.execute("SAVEPOINT service_transaction")

    def rollback(self):
        with self.raw.cursor() as cursor:
            cursor.execute("ROLLBACK TO SAVEPOINT service_transaction")

    def close(self):
        """The fixture owns and closes the underlying connection."""


@pytest.fixture
def postgres():
    engine = create_engine(settings.database_url, poolclass=NullPool)
    raw = engine.raw_connection()
    try:
        schema = (
            Path(__file__).resolve().parents[3]
            / "customer360-database" / "database-schema.sql"
        ).read_text()
        start = schema.index("-- --- Per-profile workflow action ledger")
        end = schema.index("-- --- Per-recipient email send ledger", start)
        with raw.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SET LOCAL statement_timeout = '30s'")
            cursor.execute(schema[start:end])
            cursor.execute(schema[start:end])
            tenant_id, campaign_id, profile_id = (str(uuid.uuid4()) for _ in range(3))
            cursor.execute(
                """
                INSERT INTO customer360.sys_tenant
                    (tenant_id, tenant_code, tenant_name, company_name, business_type)
                VALUES (%s, %s, 'Campaign UAT', 'Rollback test', 'Retail')
                """,
                (tenant_id, f"campaign-uat-{tenant_id}"),
            )
            cursor.execute(
                """
                INSERT INTO customer360.crm_campaign (campaign_id, tenant_id, name, status)
                VALUES (%s, %s, 'Rollback-only campaign', 'Running')
                """,
                (campaign_id, tenant_id),
            )
            cursor.execute(
                """
                INSERT INTO customer360.cdp_master_profiles
                    (master_profile_id, tenant_id, first_name)
                VALUES (%s, %s, 'UAT')
                """,
                (profile_id, tenant_id),
            )
        yield TransactionalConnection(raw), tenant_id, campaign_id, uuid.UUID(profile_id), engine
    finally:
        raw.rollback()
        raw.close()
        engine.dispose()


def test_postgres_replay_preserves_history_and_deduplicates_retry(postgres):
    connection, tenant_id, campaign_id, profile_id, _ = postgres
    old_id, replay_id = uuid.uuid4(), uuid.uuid4()
    first = WorkflowLedger(connection, tenant_id, campaign_id, "initial-run", old_id)
    assert first.begin(profile_id, "email", "email", "email")
    first.finish(profile_id, "email", "Sent", provider="mock")
    assert first.begin(profile_id, "email", "email", "email") is False
    replay = WorkflowLedger(connection, tenant_id, campaign_id, "replay-run", replay_id)
    assert replay.begin(profile_id, "email", "email", "email")
    replay.finish(profile_id, "email", "Sent", provider="mock")
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT execution_id, status, attempt_count
            FROM customer360.crm_campaign_workflow_action_runs
            WHERE tenant_id = %s AND campaign_id = %s ORDER BY created_at, execution_id
            """,
            (tenant_id, campaign_id),
        )
        rows = cursor.fetchall()
    assert len(rows) == 2
    assert {str(row[0]) for row in rows} == {str(old_id), str(replay_id)}
    assert all(row[1:] == ("Sent", 1) for row in rows)


def test_postgres_failed_action_retries_only_within_its_execution(postgres):
    connection, tenant_id, campaign_id, profile_id, _ = postgres
    execution_id = uuid.uuid4()
    ledger = WorkflowLedger(connection, tenant_id, campaign_id, "attempt-1", execution_id)
    assert ledger.begin(profile_id, "email", "email", "email")
    ledger.finish(profile_id, "email", "Failed", error="test failure")
    assert ledger.begin(profile_id, "email", "email", "email")
    ledger.finish(profile_id, "email", "Sent", provider="mock")
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT attempt_count, status FROM customer360.crm_campaign_workflow_action_runs
            WHERE tenant_id = %s AND campaign_id = %s AND execution_id = %s
            """,
            (tenant_id, campaign_id, str(execution_id)),
        )
        assert cursor.fetchone() == (2, "Sent")


def test_postgres_campaign_execution_lock_excludes_other_workers(postgres):
    connection, tenant_id, campaign_id, _, engine = postgres
    assert lock_campaign_execution(connection, tenant_id, campaign_id)
    other = engine.raw_connection()
    try:
        assert lock_campaign_execution(other, tenant_id, campaign_id) is False
        assert lock_campaign_execution(other, tenant_id, str(uuid.uuid4())) is True
    finally:
        other.rollback()
        other.close()


def test_postgres_activation_runtime_and_unlimited_replay_without_delivery(postgres, monkeypatch):
    connection, tenant_id, campaign_id, profile_id, _ = postgres
    segment_id, execution_id = str(uuid.uuid4()), str(uuid.uuid4())
    workflow = {
        "schema_version": 2,
        "root": {
            "id": "trigger", "type": "trigger", "config": {"event": "signup", "segment_id": segment_id},
            "children": [{
                "id": "email", "type": "email",
                "config": {"subject": "Hello {{first_name}}", "body": "Rollback-only UAT"},
                "children": [{
                    "id": "wait", "type": "wait_event",
                    "config": {"event": "purchase", "amount": 1, "unit": "hours"},
                    "children": [{
                        "id": "goal", "type": "exit", "config": {"kind": "goal"}, "branch": "happened",
                    }, {
                        "id": "tag", "type": "update_profile", "branch": "timeout",
                        "config": {"action": "add_tag", "tag": "uat-workflow-visited"},
                    }],
                }],
            }],
        },
    }
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO customer360.cdp_segments
                (segment_id, tenant_id, segment_name, segment_tag, status_code)
            VALUES (%s, %s, 'Rollback UAT audience', 'uat-replay-members', 1)
            """,
            (segment_id, tenant_id),
        )
        cursor.execute(
            """
            SELECT flow_id, version FROM customer360.crm_save_flow(
                %s::uuid, NULL::uuid, 'Rollback UAT flow', %s::jsonb,
                'user', NULL::uuid, NULL::varchar, '{}'::jsonb)
            """,
            (tenant_id, json.dumps(workflow)),
        )
        flow_id, version = cursor.fetchone()
        cursor.execute(
            """
            UPDATE customer360.crm_campaign
            SET approval_status = 'Approved', segment_id = %s, workflow_id = %s,
                workflow_version = %s, workflow_execution_id = %s
            WHERE tenant_id = %s AND campaign_id = %s
            """,
            (segment_id, str(flow_id), version, execution_id, tenant_id, campaign_id),
        )
        cursor.execute(
            """
            UPDATE customer360.cdp_master_profiles
            SET email = 'uat-rollback@example.com', status_code = 1,
                communication_preferences = '{"email_opt_in": true}'::jsonb,
                segmentation_tags = ARRAY['uat-replay-members']::text[]
            WHERE tenant_id = %s AND master_profile_id = %s
            """,
            (tenant_id, str(profile_id)),
        )
    connection.commit()
    monkeypatch.setattr(workflow_runner, "connect", lambda: connection)
    monkeypatch.setattr(activation, "connect", lambda: connection)
    monkeypatch.setattr(workflow_runner, "_load_connector", lambda *_: None)
    submitted = []
    monkeypatch.setattr(
        activation, "trigger_campaign_workflow_job",
        lambda *args, **kwargs: submitted.append(kwargs["execution_id"]) or "no-network-run",
    )

    execution_ids = []
    for _ in range(3):
        execution_ids.append(execution_id)
        result = workflow_runner.run_campaign_workflow(
            tenant_id=tenant_id, campaign_id=campaign_id, execution_id=execution_id,
        )
        assert result["profiles"] == result["skipped"] == result["applied"] == 1
        assert result["sent"] == result["errors"] == 0
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status FROM customer360.crm_campaign WHERE tenant_id = %s AND campaign_id = %s",
                (tenant_id, campaign_id),
            )
            assert cursor.fetchone()[0] == "Completed"
        if len(execution_ids) < 3:
            activated = activation.activate_campaign(
                campaign_id, tenant_id, restart=True, expected_execution_id=execution_id,
            )
            execution_id = activated["execution_id"]
    assert len(set(execution_ids)) == 3
    assert len(submitted) == 2
    with pytest.raises(activation.CampaignActivationError, match="execution changed"):
        activation.activate_campaign(
            campaign_id, tenant_id, restart=True, expected_execution_id=execution_ids[0],
        )
    assert len(submitted) == 2
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT execution_id, action_type, status, attempt_count
            FROM customer360.crm_campaign_workflow_action_runs
            WHERE tenant_id = %s AND campaign_id = %s
            ORDER BY created_at, action_type
            """,
            (tenant_id, campaign_id),
        )
        rows = cursor.fetchall()
    assert len(rows) == 6
    email_rows = [row for row in rows if row[1] == "email"]
    profile_rows = [row for row in rows if row[1] == "update_profile"]
    assert len(email_rows) == 3
    assert len(profile_rows) == 3
    assert all(row[2:] == ("Skipped", 1) for row in email_rows)
    assert all(row[2:] == ("Applied", 1) for row in profile_rows)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT action, before_data, after_data, success
            FROM customer360.sys_audit_log
            WHERE tenant_id = %s AND resource_type = 'crm_campaign' AND resource_id = %s
            ORDER BY created_at
            """,
            (tenant_id, campaign_id),
        )
        audit_rows = cursor.fetchall()
    actions = [row[0] for row in audit_rows]
    assert actions == [
        "WORKFLOW_COMPLETED",
        "WORKFLOW_RESTARTED",
        "WORKFLOW_COMPLETED",
        "WORKFLOW_RESTARTED",
        "WORKFLOW_COMPLETED",
    ]
    assert all(row[3] is True for row in audit_rows)
    completed_audits = [row for row in audit_rows if row[0] == "WORKFLOW_COMPLETED"]
    assert all(row[2]["profiles"] == 1 and row[2]["applied"] == 1 for row in completed_audits)


def test_postgres_audience_replays_log_separate_dry_runs_without_a_provider(postgres, monkeypatch):
    connection, tenant_id, campaign_id, profile_id, _ = postgres
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "data_synch"))
    audience_sync = importlib.import_module("audience_sync")
    monkeypatch.setattr(audience_sync, "_connect", lambda: connection)
    segment_id = str(uuid.uuid4())
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO customer360.cdp_segments
                (segment_id, tenant_id, segment_name, segment_tag, status_code)
            VALUES (%s, %s, 'UAT sync audience', 'uat-sync-members', 1)
            """,
            (segment_id, tenant_id),
        )
        cursor.execute(
            """
            UPDATE customer360.crm_campaign
            SET segment_id = %s, approval_status = 'Approved'
            WHERE tenant_id = %s AND campaign_id = %s
            """,
            (segment_id, tenant_id, campaign_id),
        )
        cursor.execute(
            """
            UPDATE customer360.cdp_master_profiles
            SET email = 'uat-sync@example.com', status_code = 1,
                segmentation_tags = ARRAY['uat-sync-members']::text[]
            WHERE tenant_id = %s AND master_profile_id = %s
            """,
            (tenant_id, str(profile_id)),
        )
    connection.commit()
    for _ in range(2):
        result = audience_sync.run_data_synch_job(
            tenant_id=tenant_id, campaign_id=campaign_id, platform="meta_ads",
            execution_id=str(uuid.uuid4()),
        )
        assert result["dry_run"] is True
        assert result["reason"] == "provider_not_configured"
        assert result["matched_count"] == result["eligible_count"] == 1
        assert result["uploaded_count"] == 0
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT status, dry_run, uploaded_count, metadata
            FROM customer360.crm_ad_sync_runs WHERE tenant_id = %s AND campaign_id = %s
            """,
            (tenant_id, campaign_id),
        )
        rows = cursor.fetchall()
    assert len(rows) == 2
    assert all(row[:3] == ("Completed", True, 0) for row in rows)
    assert all(row[3]["reason"] == "provider_not_configured" for row in rows)
