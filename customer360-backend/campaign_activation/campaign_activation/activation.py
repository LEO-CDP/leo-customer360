"""Activation boundary for persisted campaign workflows.

An approved campaign must point to an approved workflow version and a selected
segment. Activation marks it ``Running`` and submits ``campaign_workflow_job``.
That job loads the profiles, evaluates the graph, and executes every action
node.
"""

import logging
from typing import Any, Callable, Optional
import uuid

from psycopg2.extras import Json, RealDictCursor

from .db import DB_SCHEMA, connect
from .rls import lock_campaign_execution, set_tenant_context
from .triggers import trigger_campaign_workflow_job

logger = logging.getLogger(__name__)


class CampaignActivationError(Exception):
    """Raised when a workflow campaign cannot be activated safely."""


def _record_campaign_audit_event(
    cur,
    *,
    tenant_id: str,
    campaign_id: str,
    action: str,
    before_data: dict[str, Any] | None = None,
    after_data: dict[str, Any] | None = None,
    success: bool = True,
    error_message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Persist a runtime state transition into sys_audit_log for campaign observability."""
    cur.execute(
        f"""
        INSERT INTO {DB_SCHEMA}.sys_audit_log
            (tenant_id, action, resource_type, resource_id, service_name,
             before_data, after_data, success, error_message, metadata,
             created_at)
        VALUES (%(tenant_id)s, %(action)s, 'crm_campaign', %(campaign_id)s,
                'campaign_activation', %(before_data)s, %(after_data)s,
                %(success)s, %(error_message)s, %(metadata)s,
                timezone('UTC', now()))
        """,
        {
            "tenant_id": tenant_id,
            "action": action,
            "campaign_id": str(campaign_id),
            "before_data": Json(before_data) if before_data is not None else None,
            "after_data": Json(after_data) if after_data is not None else None,
            "success": success,
            "error_message": error_message[:2000] if error_message else None,
            "metadata": Json(metadata or {}),
        },
    )


def _load_campaign(cur, tenant_id: str, campaign_id: str) -> Optional[dict]:
    cur.execute(
        f"""
        SELECT c.campaign_id, c.name, c.approval_status, c.status,
               c.segment_id, c.workflow_id, c.workflow_version,
               c.workflow_execution_id,
               v.approval_status AS workflow_approval_status
        FROM {DB_SCHEMA}.crm_campaign c
        LEFT JOIN {DB_SCHEMA}.crm_flow_version v
          ON v.tenant_id = c.tenant_id
         AND v.flow_id = c.workflow_id
         AND v.version = c.workflow_version
        WHERE c.campaign_id = %(campaign_id)s
          AND c.tenant_id = %(tenant_id)s
        FOR UPDATE OF c
        """,
        {"campaign_id": campaign_id, "tenant_id": tenant_id},
    )
    return cur.fetchone()


def activate_campaign(
    campaign_id: str,
    tenant_id: str,
    *,
    restart: bool = False,
    expected_execution_id: str | None = None,
    log: Callable[[str], None] = logger.info,
) -> dict:
    """Start, resume a failed execution, or explicitly replay a completed campaign."""
    try:
        campaign_id = str(uuid.UUID(str(campaign_id)))
        tenant_id = str(uuid.UUID(str(tenant_id)))
        if expected_execution_id is not None:
            expected_execution_id = str(uuid.UUID(str(expected_execution_id)))
    except (ValueError, TypeError, AttributeError):
        # Preserve string IDs for synthetic unit-test fixtures while normalizing valid UUIDs.
        campaign_id = str(campaign_id).strip()
        tenant_id = str(tenant_id).strip()
        if not campaign_id or not tenant_id:
            raise ValueError("campaign_id and tenant_id must be non-empty")

    conn = connect()
    try:
        if not lock_campaign_execution(conn, tenant_id, campaign_id):
            raise CampaignActivationError("Campaign is already executing; retry after it finishes")
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            set_tenant_context(cur, tenant_id)
            campaign = _load_campaign(cur, tenant_id, campaign_id)
            if campaign is None:
                raise CampaignActivationError(f"campaign {campaign_id} not found for tenant {tenant_id}")
            campaign_status = campaign.get("status")
            previous_execution_id = (
                str(campaign["workflow_execution_id"])
                if campaign.get("workflow_execution_id") is not None
                else None
            )
            if campaign_status == "Running":
                raise CampaignActivationError(
                    f"campaign {campaign_id} is already {campaign['status']}"
                )
            if campaign_status == "Completed" and not restart:
                raise CampaignActivationError(
                    f"campaign {campaign_id} is Completed; explicit restart is required"
                )
            if restart and campaign_status == "Completed" and (
                previous_execution_id != (str(expected_execution_id) if expected_execution_id is not None else None)
            ):
                raise CampaignActivationError("Campaign execution changed; refresh before restarting")
            if restart and campaign_status not in {"Completed", "Failed"}:
                raise CampaignActivationError(
                    f"campaign {campaign_id} can only be restarted after it is Completed"
                )
            if campaign.get("approval_status") != "Approved":
                raise CampaignActivationError(
                    f"campaign {campaign_id} is not Approved "
                    f"(approval_status={campaign.get('approval_status')!r})"
                )
            if not campaign.get("workflow_id") or campaign.get("workflow_version") is None:
                raise CampaignActivationError(f"campaign {campaign_id} has no pinned workflow version")
            if campaign.get("workflow_approval_status") != "Approved":
                raise CampaignActivationError(
                    f"workflow {campaign['workflow_id']} version {campaign['workflow_version']} "
                    f"is not Approved "
                    f"(approval_status={campaign.get('workflow_approval_status')!r})"
                )
            if not campaign.get("segment_id"):
                raise CampaignActivationError(f"campaign {campaign_id} has no segment_id")

            cur.execute(
                f"""
                SELECT segment_tag
                FROM {DB_SCHEMA}.cdp_segments
                WHERE segment_id = %(segment_id)s
                  AND tenant_id = %(tenant_id)s
                  AND is_active
                  AND status_code = 1
                """,
                {"segment_id": str(campaign["segment_id"]), "tenant_id": tenant_id},
            )
            segment = cur.fetchone()
            if not segment or not segment.get("segment_tag"):
                raise CampaignActivationError(
                    f"segment {campaign['segment_id']} not found or inactive"
                )

            if campaign_status == "Completed" and restart:
                execution_id = uuid.uuid4()
            elif campaign.get("workflow_execution_id"):
                execution_id = uuid.UUID(str(campaign["workflow_execution_id"]))
            else:
                execution_id = uuid.uuid4()
            cur.execute(
                f"""
                UPDATE {DB_SCHEMA}.crm_campaign
                SET status = 'Running', workflow_execution_id = %(execution_id)s,
                    updated_at = now()
                WHERE campaign_id = %(campaign_id)s
                  AND tenant_id = %(tenant_id)s
                """,
                {
                    "campaign_id": campaign_id,
                    "tenant_id": tenant_id,
                    "execution_id": str(execution_id),
                },
            )
            _record_campaign_audit_event(
                cur,
                tenant_id=tenant_id,
                campaign_id=campaign_id,
                action="WORKFLOW_RESTARTED" if (campaign_status == "Completed" and restart) else "WORKFLOW_ACTIVATED",
                before_data={
                    "status": campaign_status,
                    "workflow_execution_id": previous_execution_id,
                },
                after_data={
                    "status": "Running",
                    "workflow_execution_id": str(execution_id),
                    "workflow_version": campaign.get("workflow_version"),
                },
                metadata={
                    "restart": bool(restart),
                    "segment_id": str(campaign["segment_id"]),
                    "workflow_id": str(campaign["workflow_id"]),
                    "workflow_version": campaign.get("workflow_version"),
                },
            )
        conn.commit()

        log(
            f"campaign_activation: campaign {campaign_id} marked Running "
            f"(execution_id={execution_id}, restart={restart})"
        )
        try:
            workflow_run_id = trigger_campaign_workflow_job(
                campaign_id, tenant_id, execution_id=str(execution_id), log=log
            )
        except Exception as exc:
            with conn.cursor() as cur:
                set_tenant_context(cur, tenant_id)
                cur.execute(
                    f"""
                    UPDATE {DB_SCHEMA}.crm_campaign
                    SET status = 'Failed', updated_at = now()
                    WHERE campaign_id = %(campaign_id)s
                      AND tenant_id = %(tenant_id)s
                      AND workflow_execution_id = %(execution_id)s
                    """,
                    {
                        "campaign_id": campaign_id,
                        "tenant_id": tenant_id,
                        "execution_id": str(execution_id),
                    },
                )
                _record_campaign_audit_event(
                    cur,
                    tenant_id=tenant_id,
                    campaign_id=campaign_id,
                    action="WORKFLOW_SUBMISSION_FAILED",
                    before_data={
                        "status": "Running",
                        "workflow_execution_id": str(execution_id),
                    },
                    after_data={
                        "status": "Failed",
                        "workflow_execution_id": str(execution_id),
                    },
                    success=False,
                    error_message=str(exc),
                )
            conn.commit()
            raise
        return {
            "campaign_id": campaign_id,
            "tenant_id": tenant_id,
            "status": "Running",
            "workflow_run_id": workflow_run_id,
            "execution_id": str(execution_id),
        }
    finally:
        conn.close()
