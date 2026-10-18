"""Execution for persisted campaign engagement workflows.

Workflow branches choose different content, channels, waits, tags, and
campaign-scoped audience-sync actions. Every per-profile outbound action is
guarded by a ledger before a provider call.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any
from urllib.request import Request, urlopen

from psycopg2.extras import RealDictCursor
from dagster_graphql import DagsterGraphQLClient

from leo_customer360_dao.models.identity import CdpMasterProfile
from leo_customer360_dao.workflow import FlowDefinition, FlowEngine
from leo_customer360_dao.workflow.services import ChannelGateway, EventSource, ProfileWriter, Services
from leo_customer360_dao.workflow.models import DeliveryReceipt, OutboundMessage

from .db import DB_SCHEMA, connect
from .rls import set_tenant_context

logger = logging.getLogger(__name__)


class CampaignWorkflowError(RuntimeError):
    """Raised when a campaign workflow cannot be executed safely."""


def _workflow_sync_targets(workflow: dict[str, Any]) -> list[dict[str, str]]:
    targets: dict[tuple[str, str], dict[str, str]] = {}

    def visit(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "run_data_synch_job":
            config = node.get("config") or {}
            platform = str(config.get("platform") or "").strip().lower()
            audience_name = str(config.get("audience_name") or "").strip()[:255]
            if platform and audience_name:
                targets[(platform, audience_name.lower())] = {
                    "platform": platform,
                    "audience_name": audience_name,
                }
        for child in node.get("children") or []:
            visit(child)

    visit(workflow.get("root"))
    return list(targets.values())


def _submit_audience_sync_jobs(
    *,
    tenant_id: str,
    campaign_id: str,
    segment_id: str,
    workflow: dict[str, Any],
    log: Any,
) -> list[str]:
    """Activate each persisted audience-sync action once for this workflow run."""
    host = os.environ.get("DAGSTER_GRAPHQL_HOST", "localhost")
    port = int(os.environ.get("DAGSTER_GRAPHQL_PORT", "3000"))
    job_name = os.environ.get("DAGSTER_DATA_SYNCH_JOB_NAME", "data_synch_job")
    location = os.environ.get("DAGSTER_DATA_SYNCH_LOCATION_NAME", "data_synch")
    run_ids = []
    for target in _workflow_sync_targets(workflow):
        try:
            client = DagsterGraphQLClient(host, port_number=port)
            run_id = client.submit_job_execution(
                job_name,
                repository_location_name=location,
                run_config={
                    "ops": {
                        "run_data_synch_op": {
                            "config": {
                                "tenant_id": tenant_id,
                                "campaign_id": campaign_id,
                                "segment_id": segment_id,
                                "platform": target["platform"],
                                "audience_name": target["audience_name"],
                                "dry_run": False,
                                "batch_size": 1000,
                            }
                        }
                    }
                },
                tags={
                    "tenant_id": tenant_id,
                    "campaign_id": campaign_id,
                    "platform": target["platform"],
                    "sync_type": "workflow_action",
                },
            )
        except Exception as exc:  # noqa: BLE001 - fail the workflow run on action submission failure.
            raise CampaignWorkflowError(
                f"could not submit audience-sync action for {target['platform']}: {exc}"
            ) from exc
        run_ids.append(run_id)
        log(
            "campaign_workflow: submitted audience-sync action "
            f"platform={target['platform']} run_id={run_id}"
        )
    return run_ids


@dataclass
class Connector:
    provider: str
    config: dict[str, Any]
    credentials: dict[str, Any]


class WorkflowLedger:
    """Persist per-profile/node idempotency around provider calls."""

    def __init__(self, connection, tenant_id: str, campaign_id: str, run_id: str | None):
        self.connection = connection
        self.tenant_id = tenant_id
        self.campaign_id = campaign_id
        self.run_id = run_id

    def begin(self, profile_id: uuid.UUID, node_id: str, action_type: str, channel: str) -> bool:
        with self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
            set_tenant_context(cursor, self.tenant_id)
            cursor.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS acquired",
                (f"{self.campaign_id}:{profile_id}:{node_id}",),
            )
            if not cursor.fetchone()["acquired"]:
                self.connection.commit()
                return False
            cursor.execute(
                f"""
                SELECT status
                FROM {DB_SCHEMA}.crm_campaign_workflow_action_runs
                WHERE campaign_id = %(campaign_id)s
                  AND master_profile_id = %(profile_id)s
                  AND node_id = %(node_id)s
                FOR UPDATE
                """,
                {
                    "campaign_id": self.campaign_id,
                    "profile_id": str(profile_id),
                    "node_id": node_id,
                },
            )
            row = cursor.fetchone()
            if row and row["status"] in {"Sent", "Applied", "Skipped"}:
                self.connection.commit()
                with self.connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                        (f"{self.campaign_id}:{profile_id}:{node_id}",),
                    )
                self.connection.commit()
                return False
            if row:
                cursor.execute(
                    f"""
                    UPDATE {DB_SCHEMA}.crm_campaign_workflow_action_runs
                    SET status = 'Running', attempt_count = attempt_count + 1,
                        error_message = NULL, run_id = %(run_id)s, updated_at = now()
                    WHERE campaign_id = %(campaign_id)s
                      AND master_profile_id = %(profile_id)s
                      AND node_id = %(node_id)s
                    """,
                    {
                        "campaign_id": self.campaign_id,
                        "profile_id": str(profile_id),
                        "node_id": node_id,
                        "run_id": self.run_id,
                    },
                )
            else:
                cursor.execute(
                    f"""
                    INSERT INTO {DB_SCHEMA}.crm_campaign_workflow_action_runs
                        (tenant_id, campaign_id, master_profile_id, node_id,
                         action_type, channel, run_id)
                    VALUES (%(tenant_id)s, %(campaign_id)s, %(profile_id)s, %(node_id)s,
                            %(action_type)s, %(channel)s, %(run_id)s)
                    """,
                    {
                        "tenant_id": self.tenant_id,
                        "campaign_id": self.campaign_id,
                        "profile_id": str(profile_id),
                        "node_id": node_id,
                        "action_type": action_type,
                        "channel": channel,
                        "run_id": self.run_id,
                    },
                )
            self.connection.commit()
            return True

    def finish(
        self,
        profile_id: uuid.UUID,
        node_id: str,
        status: str,
        *,
        provider: str | None = None,
        provider_message_id: str | None = None,
        error: str | None = None,
    ) -> None:
        with self.connection.cursor() as cursor:
            set_tenant_context(cursor, self.tenant_id)
            cursor.execute(
                f"""
                UPDATE {DB_SCHEMA}.crm_campaign_workflow_action_runs
                SET status = %(status)s, provider = %(provider)s,
                    provider_message_id = %(provider_message_id)s,
                    error_message = %(error)s, updated_at = now()
                WHERE campaign_id = %(campaign_id)s
                  AND master_profile_id = %(profile_id)s
                  AND node_id = %(node_id)s
                """,
                {
                    "status": status,
                    "provider": provider,
                    "provider_message_id": provider_message_id,
                    "error": error,
                    "campaign_id": self.campaign_id,
                    "profile_id": str(profile_id),
                    "node_id": node_id,
                },
            )
        self.connection.commit()
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                (f"{self.campaign_id}:{profile_id}:{node_id}",),
            )
        self.connection.commit()


class WorkflowGateway(ChannelGateway):
    """Base gateway that adds ledger idempotency to a channel provider."""

    channel = "unknown"
    action_type = "unknown"

    def __init__(self, ledger: WorkflowLedger):
        self.ledger = ledger

    def _provider_name(self, message: OutboundMessage) -> str:
        return self.provider_name

    def send(self, message: OutboundMessage) -> DeliveryReceipt:
        if not self.ledger.begin(message.profile_id, message.node_id, self.action_type, self.channel):
            return DeliveryReceipt(status="skipped", detail="already completed")
        try:
            receipt = self._send(message)
        except Exception as exc:  # provider failures are recorded per action
            self.ledger.finish(message.profile_id, message.node_id, "Failed", error=str(exc)[:2000])
            return DeliveryReceipt(status="failed", detail=str(exc)[:500])
        ledger_status = {
            "sent": "Sent",
            "skipped": "Skipped",
        }.get(receipt.status, "Failed")
        self.ledger.finish(
            message.profile_id,
            message.node_id,
            ledger_status,
            provider=self._provider_name(message),
            provider_message_id=receipt.provider_id,
            error=receipt.detail if ledger_status == "Failed" else None,
        )
        return receipt

    def _send(self, message: OutboundMessage) -> DeliveryReceipt:
        raise NotImplementedError


class MockWorkflowGateway(WorkflowGateway):
    provider_name = "workflow_mock"

    def _send(self, message: OutboundMessage) -> DeliveryReceipt:
        return DeliveryReceipt(status="sent", provider_id=f"workflow-mock-{uuid.uuid4()}")


class EmailWorkflowGateway(WorkflowGateway):
    channel = "email"
    action_type = "email"

    def __init__(self, ledger: WorkflowLedger, connector: Connector):
        super().__init__(ledger)
        self.connector = connector
        self.provider_name = connector.provider or "mock"

    def _send(self, message: OutboundMessage) -> DeliveryReceipt:
        if self.provider_name == "mock":
            return DeliveryReceipt(status="sent", provider_id=f"workflow-email-mock-{uuid.uuid4()}")
        if self.provider_name != "smtp":
            raise CampaignWorkflowError(f"Unsupported email workflow provider {self.provider_name!r}")
        config = {**self.connector.config, **self.connector.credentials}
        host = str(config.get("smtp_host") or "localhost")
        port = int(config.get("smtp_port") or 587)
        use_tls = bool(config.get("smtp_use_tls", True))
        sender = str(config.get("from_address") or "no-reply@leocdp.local")
        sender_name = str(config.get("from_name") or "LEO CDP")
        email = EmailMessage()
        email["From"] = formataddr((sender_name, sender))
        email["To"] = message.to or ""
        email["Subject"] = message.subject or ""
        email.set_content(message.body or "")
        with smtplib.SMTP(host, port, timeout=30) as server:
            if use_tls:
                server.starttls(context=ssl.create_default_context())
            if config.get("smtp_username") and config.get("smtp_password"):
                server.login(str(config["smtp_username"]), str(config["smtp_password"]))
            server.send_message(email)
        return DeliveryReceipt(status="sent", provider_id=f"smtp-{uuid.uuid4()}")


class WebhookWorkflowGateway(WorkflowGateway):
    """SMS/web-push gateway for configured webhook or OneSignal providers."""

    def __init__(self, ledger: WorkflowLedger, channel: str, connector: Connector, tokens: dict[str, list[str]]):
        super().__init__(ledger)
        self.channel = channel
        self.action_type = channel
        self.connector = connector
        self.tokens = tokens
        self.provider_name = connector.provider or "mock"

    def _send(self, message: OutboundMessage) -> DeliveryReceipt:
        if self.provider_name == "mock":
            return DeliveryReceipt(status="sent", provider_id=f"workflow-{self.channel}-mock-{uuid.uuid4()}")
        config = {**self.connector.config, **self.connector.credentials}
        if self.provider_name == "onesignal" and self.channel == "push":
            target_ids = self.tokens.get(str(message.profile_id), [])
            if not target_ids:
                return DeliveryReceipt(status="skipped", detail="no push token")
            payload = {
                "app_id": config["app_id"],
                "include_subscription_ids": target_ids,
                "headings": {"en": message.subject or ""},
                "contents": {"en": message.body or ""},
            }
            request = Request(
                str(config.get("api_url") or "https://onesignal.com/api/v1/notifications"),
                data=json.dumps(payload).encode(),
                headers={
                    "Authorization": f"Basic {config['api_key']}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
        else:
            url = str(config.get("webhook_url") or "")
            if not url:
                raise CampaignWorkflowError(f"{self.channel} connector is missing webhook_url")
            payload = {"to": message.to, "subject": message.subject, "body": message.body}
            request = Request(
                url,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        with urlopen(request, timeout=30) as response:
            response_body = response.read(4096).decode("utf-8", errors="replace")
        return DeliveryReceipt(status="sent", provider_id=f"{self.provider_name}-{uuid.uuid4()}", detail=response_body[:100])


class NotificationWorkflowGateway(WorkflowGateway):
    """Route Send notification through SMS, Zalo OA, WhatsApp, or mobile-native."""

    channel = "notification"
    action_type = "notification"

    def __init__(
        self,
        ledger: WorkflowLedger,
        connectors: dict[str, Connector],
        tokens: dict[str, list[str]],
    ):
        super().__init__(ledger)
        self.connectors = connectors
        self.tokens = tokens

    def _provider_name(self, message: OutboundMessage) -> str:
        return message.provider or "sms"

    def _send(self, message: OutboundMessage) -> DeliveryReceipt:
        provider = message.provider or "sms"
        connector = self.connectors.get(provider)
        if connector is None:
            raise CampaignWorkflowError(f"Notification provider {provider!r} is not configured")
        provider_name = connector.provider or "mock"
        if provider_name == "mock":
            return DeliveryReceipt(
                status="sent",
                provider_id=f"workflow-{provider}-mock-{uuid.uuid4()}",
            )
        config = {**connector.config, **connector.credentials}
        if provider == "mobile_native":
            target_ids = self.tokens.get(str(message.profile_id), [])
            if not target_ids:
                return DeliveryReceipt(status="skipped", detail="no mobile token")
            payload = {
                "app_id": config["app_id"],
                "include_subscription_ids": target_ids,
                "headings": {"en": message.subject or ""},
                "contents": {"en": message.body},
            }
            request = Request(
                str(config.get("api_url") or "https://onesignal.com/api/v1/notifications"),
                data=json.dumps(payload).encode(),
                headers={
                    "Authorization": f"Basic {config['api_key']}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
        else:
            webhook_url = str(config.get("webhook_url") or "")
            if not webhook_url:
                raise CampaignWorkflowError(
                    f"{provider} notification connector is missing webhook_url"
                )
            payload = {
                "provider": provider,
                "to": message.to,
                "subject": message.subject,
                "body": message.body,
            }
            request = Request(
                webhook_url,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        with urlopen(request, timeout=30) as response:
            response_body = response.read(4096).decode("utf-8", errors="replace")
        return DeliveryReceipt(
            status="sent",
            provider_id=f"{provider_name}-{uuid.uuid4()}",
            detail=response_body[:100],
        )


class WorkflowProfileWriter(ProfileWriter):
    def __init__(self, connection, tenant_id: str):
        self.connection = connection
        self.tenant_id = tenant_id

    def apply(self, profile: CdpMasterProfile, action: str, tag: str) -> None:
        with self.connection.cursor() as cursor:
            set_tenant_context(cursor, self.tenant_id)
            if action == "add_tag":
                cursor.execute(
                    f"""
                    UPDATE {DB_SCHEMA}.cdp_master_profiles
                    SET segmentation_tags = ARRAY(
                        SELECT DISTINCT value
                        FROM unnest(COALESCE(segmentation_tags, ARRAY[]::text[]) || ARRAY[%(tag)s]::text[]) AS value
                    ), updated_at = now()
                    WHERE tenant_id = %(tenant_id)s AND master_profile_id = %(profile_id)s
                    """,
                    {"tag": tag, "tenant_id": self.tenant_id, "profile_id": str(profile.master_profile_id)},
                )
            elif action == "remove_tag":
                cursor.execute(
                    f"""
                    UPDATE {DB_SCHEMA}.cdp_master_profiles
                    SET segmentation_tags = array_remove(COALESCE(segmentation_tags, ARRAY[]::text[]), %(tag)s),
                        updated_at = now()
                    WHERE tenant_id = %(tenant_id)s AND master_profile_id = %(profile_id)s
                    """,
                    {"tag": tag, "tenant_id": self.tenant_id, "profile_id": str(profile.master_profile_id)},
                )
            else:
                raise CampaignWorkflowError(f"Unsupported profile action {action!r}")
        self.connection.commit()


class DatabaseEventSource(EventSource):
    """Resolve future raw events linked to a master profile when available."""

    def __init__(self, connection, tenant_id: str, started_at: datetime):
        self.connection = connection
        self.tenant_id = tenant_id
        self.started_at = started_at

    def occurs(self, profile, event: str, start_hours: float, limit_hours: float) -> float | None:
        start = self.started_at + timedelta(hours=start_hours)
        end = start + timedelta(hours=limit_hours)
        with self.connection.cursor() as cursor:
            set_tenant_context(cursor, self.tenant_id)
            cursor.execute(
                f"""
                SELECT min(r.event_time) AS event_time
                FROM {DB_SCHEMA}.cdp_raw_profiles_stage r
                JOIN {DB_SCHEMA}.cdp_profile_links l
                  ON l.tenant_id = r.tenant_id
                 AND l.raw_profile_id = r.raw_profile_id
                 AND l.master_profile_id = %(profile_id)s
                 AND l.status = 'ACTIVE'
                WHERE r.tenant_id = %(tenant_id)s
                  AND replace(r.event_name, '-', '_') = %(event)s
                  AND r.event_time >= %(start)s
                  AND r.event_time <= %(end)s
                """,
                {
                    "tenant_id": self.tenant_id,
                    "profile_id": str(profile.master_profile_id),
                    "event": event,
                    "start": start,
                    "end": end,
                },
            )
            row = cursor.fetchone()
        if not row or row["event_time"] is None:
            return None
        return (row["event_time"] - self.started_at).total_seconds() / 3600


def _load_connector(
    cursor,
    tenant_id: str,
    connector_type: str,
    provider: str | None = None,
) -> Connector:
    provider_clause = ""
    params = {"tenant_id": tenant_id, "connector_type": connector_type}
    if provider:
        provider_clause = " AND lower(provider) = lower(%(provider)s)"
        params["provider"] = provider
    cursor.execute(
        f"""
        SELECT provider, config, credentials
        FROM {DB_SCHEMA}.crm_connector_config
        WHERE tenant_id = %(tenant_id)s
          AND connector_type = %(connector_type)s
          {provider_clause}
          AND status = 'ACTIVE' AND is_active
        ORDER BY is_default DESC, updated_at DESC
        LIMIT 1
        """,
        params,
    )
    row = cursor.fetchone()
    return Connector(
        provider=str((row or {}).get("provider") or "mock").lower(),
        config=dict((row or {}).get("config") or {}),
        credentials=dict((row or {}).get("credentials") or {}),
    )


def _profile_from_row(row: dict[str, Any]) -> CdpMasterProfile:
    return CdpMasterProfile(**dict(row))


def _set_campaign_status(connection, tenant_id: str, campaign_id: str, status: str) -> None:
    with connection.cursor() as cursor:
        set_tenant_context(cursor, tenant_id)
        cursor.execute(
            f"""
            UPDATE {DB_SCHEMA}.crm_campaign
            SET status = %(status)s
            WHERE tenant_id = %(tenant_id)s
              AND campaign_id = %(campaign_id)s
            """,
            {"status": status, "tenant_id": tenant_id, "campaign_id": campaign_id},
        )
    connection.commit()


def _iter_profile_rows(connection, tenant_id: str, segment_tag: str, batch_size: int):
    last_id = "00000000-0000-0000-0000-000000000000"
    while True:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            set_tenant_context(cursor, tenant_id)
            cursor.execute(
                f"""
                SELECT p.master_profile_id, p.tenant_id, p.full_name, p.first_name, p.last_name,
                       p.email, p.phone_number, p.push_tokens, p.communication_preferences,
                       p.segmentation_tags, p.domain, p.engagement_score,
                       p.churn_probability, p.lead_conversion_probability, p.historical_clv,
                       p.latest_nps_score, p.average_csat, p.overall_sentiment_score,
                       p.profile_completeness_score, p.identity_confidence_score,
                       p.status_code
                FROM {DB_SCHEMA}.cdp_master_profiles p
                WHERE p.tenant_id = %(tenant_id)s
                  AND p.status_code = 1
                  AND %(segment_tag)s = ANY(p.segmentation_tags)
                  AND p.master_profile_id > CAST(%(last_id)s AS uuid)
                ORDER BY p.master_profile_id
                LIMIT %(batch_size)s
                """,
                {
                    "tenant_id": tenant_id,
                    "segment_tag": segment_tag,
                    "last_id": last_id,
                    "batch_size": batch_size,
                },
            )
            rows = cursor.fetchall()
        if not rows:
            return
        for row in rows:
            yield row
        last_id = str(rows[-1]["master_profile_id"])
        if len(rows) < batch_size:
            return


def run_campaign_workflow(
    *,
    tenant_id: str,
    campaign_id: str,
    run_id: str | None = None,
    batch_size: int = 500,
) -> dict[str, Any]:
    """Execute an approved campaign workflow for its tenant-owned segment."""
    conn = connect()
    started_at = datetime.now(timezone.utc)
    summary = {
        "campaign_id": campaign_id,
        "tenant_id": tenant_id,
        "profiles": 0,
        "errors": 0,
        "sent": 0,
        "applied": 0,
        "data_sync_run_ids": [],
    }
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            set_tenant_context(cursor, tenant_id)
            cursor.execute(
                f"""
                SELECT c.campaign_id, c.approval_status, c.segment_id,
                       c.workflow_id, c.workflow_version, v.approval_status AS workflow_approval_status,
                       s.segment_tag,
                       {DB_SCHEMA}.crm_load_flow(c.tenant_id, c.workflow_id, c.workflow_version) AS workflow
                FROM {DB_SCHEMA}.crm_campaign c
                JOIN {DB_SCHEMA}.cdp_segments s
                  ON s.tenant_id = c.tenant_id AND s.segment_id = c.segment_id
                LEFT JOIN {DB_SCHEMA}.crm_flow_version v
                  ON v.tenant_id = c.tenant_id
                 AND v.flow_id = c.workflow_id
                 AND v.version = c.workflow_version
                WHERE c.tenant_id = %(tenant_id)s
                  AND c.campaign_id = %(campaign_id)s
                  AND c.status = 'Running'
                """,
                {"tenant_id": tenant_id, "campaign_id": campaign_id},
            )
            campaign = cursor.fetchone()
            if not campaign:
                raise CampaignWorkflowError("Campaign not found for tenant")
            if campaign["approval_status"] != "Approved":
                raise CampaignWorkflowError("Campaign must be Approved before workflow execution")
            if campaign["workflow_approval_status"] != "Approved":
                raise CampaignWorkflowError("Pinned workflow version must be Approved before execution")
            if not campaign["workflow"]:
                raise CampaignWorkflowError("Campaign has no persisted workflow")
            definition = FlowDefinition.model_validate(campaign["workflow"])
            problems = FlowEngine(definition).problems()
            if problems:
                raise CampaignWorkflowError("Workflow is invalid: " + "; ".join(problems))
        with conn.cursor(cursor_factory=RealDictCursor) as connector_cursor:
            email_connector = _load_connector(connector_cursor, tenant_id, "EMAIL")
            notification_connectors = {
                "sms": _load_connector(connector_cursor, tenant_id, "SMS"),
                "zalo_oa": _load_connector(connector_cursor, tenant_id, "CHAT", "ZALO"),
                "whatsapp": _load_connector(connector_cursor, tenant_id, "CHAT", "WHATSAPP"),
                "mobile_native": _load_connector(connector_cursor, tenant_id, "PUSH"),
            }
            push_connector = _load_connector(connector_cursor, tenant_id, "PUSH")
        ledger = WorkflowLedger(conn, tenant_id, campaign_id, run_id)
        token_map: dict[str, list[str]] = {}
        services = Services(
            gateways={
                "email": EmailWorkflowGateway(ledger, email_connector),
                "notification": NotificationWorkflowGateway(ledger, notification_connectors, token_map),
                "push": WebhookWorkflowGateway(ledger, "push", push_connector, token_map),
            },
            events=DatabaseEventSource(conn, tenant_id, started_at),
            profile_writer=WorkflowProfileWriter(conn, tenant_id),
        )
        engine = FlowEngine(definition, services=services)
        for row in _iter_profile_rows(conn, tenant_id, campaign["segment_tag"], batch_size):
            token_map[str(row["master_profile_id"])] = list((row.get("push_tokens") or {}).values())
            summary["profiles"] += 1
            result = engine.run(_profile_from_row(row))
            if result.end == "error":
                summary["errors"] += 1
            summary["sent"] += sum(status == "sent" for _, status in result.deliveries)
            summary["applied"] += sum(step.node_type == "update_profile" for step in result.steps)
        summary["data_sync_run_ids"] = _submit_audience_sync_jobs(
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            segment_id=str(campaign["segment_id"]),
            workflow=campaign["workflow"],
            log=logger.info,
        )
        _set_campaign_status(
            conn,
            tenant_id,
            campaign_id,
            "Failed" if summary["errors"] else "Completed",
        )
        return summary
    except Exception:
        try:
            _set_campaign_status(conn, tenant_id, campaign_id, "Failed")
        except Exception:
            conn.rollback()
            logger.exception("Could not mark failed campaign workflow %s", campaign_id)
        raise
    finally:
        conn.close()
