"""Activation boundary for persisted campaign workflows.

An approved campaign must point to an approved workflow version and a selected
segment. Activation marks it ``Running`` and submits ``campaign_workflow_job``.
That job loads the profiles, evaluates the graph, and executes every action
node.
"""

import logging
from typing import Callable, Optional
import uuid

from psycopg2.extras import RealDictCursor

from .db import DB_SCHEMA, connect
from .rls import lock_campaign_execution, set_tenant_context
from .triggers import trigger_campaign_workflow_job

logger = logging.getLogger(__name__)


class CampaignActivationError(Exception):
    """Raised when a workflow campaign cannot be activated safely."""


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
            if campaign_status == "Running":
                raise CampaignActivationError(
                    f"campaign {campaign_id} is already {campaign['status']}"
                )
            if campaign_status == "Completed" and not restart:
                raise CampaignActivationError(
                    f"campaign {campaign_id} is Completed; explicit restart is required"
                )
            if restart and campaign_status == "Completed" and (
                str(campaign.get("workflow_execution_id")) != str(expected_execution_id)
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
                SET status = 'Running', workflow_execution_id = %(execution_id)s
                WHERE campaign_id = %(campaign_id)s
                  AND tenant_id = %(tenant_id)s
                """,
                {
                    "campaign_id": campaign_id,
                    "tenant_id": tenant_id,
                    "execution_id": str(execution_id),
                },
            )
        conn.commit()

        log(f"campaign_activation: campaign {campaign_id} marked Running")
        try:
            workflow_run_id = trigger_campaign_workflow_job(
                campaign_id, tenant_id, execution_id=str(execution_id), log=log
            )
        except Exception:
            with conn.cursor() as cur:
                set_tenant_context(cur, tenant_id)
                cur.execute(
                    f"""
                    UPDATE {DB_SCHEMA}.crm_campaign
                    SET status = 'Failed'
                    WHERE campaign_id = %(campaign_id)s
                      AND tenant_id = %(tenant_id)s
                    """,
                    {"campaign_id": campaign_id, "tenant_id": tenant_id},
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
