"""Campaign activation logic.

``activate_campaign`` is the real body behind ``campaign_activation_job``:

  1. load the campaign and validate it is human-Approved and points at an
     Approved template + a segment,
  2. snapshot the target segment size (segment_tag = ANY(segmentation_tags)),
  3. mark the campaign execution status Running (bookkeeping distinct from the
     human-approval gate owned elsewhere),
  4. hand off to the email dispatch flow by submitting ``email_engine_job``.

Idempotent: re-activating an already-Running campaign just re-submits the
email_engine run, which is itself idempotent per recipient.
"""

import logging
from typing import Callable, Optional

from psycopg2.extras import RealDictCursor

from .db import DB_SCHEMA, connect
from .rls import set_tenant_context
from .triggers import trigger_email_engine_job

logger = logging.getLogger(__name__)


class CampaignActivationError(Exception):
    """Raised when a campaign cannot be activated (not Approved, no template /
    segment, template not Approved). Surfaces as a Dagster op failure."""


def _load_campaign(cur, tenant_id: str, campaign_id: str) -> Optional[dict]:
    cur.execute(
        f"""
        SELECT campaign_id, name, approval_status, status, segment_id, template_id
        FROM {DB_SCHEMA}.crm_campaign
        WHERE campaign_id = %(campaign_id)s AND tenant_id = %(tenant_id)s
        """,
        {"campaign_id": campaign_id, "tenant_id": tenant_id},
    )
    return cur.fetchone()


def _template_status(cur, tenant_id: str, template_id: str) -> Optional[str]:
    cur.execute(
        f"SELECT status FROM {DB_SCHEMA}.crm_email_templates "
        f"WHERE template_id = %(template_id)s AND tenant_id = %(tenant_id)s",
        {"template_id": template_id, "tenant_id": tenant_id},
    )
    row = cur.fetchone()
    return row["status"] if row else None


def _segment_snapshot_count(cur, tenant_id: str, segment_id: str) -> int:
    cur.execute(
        f"""
        SELECT count(*) AS n
        FROM {DB_SCHEMA}.cdp_master_profiles p
        JOIN {DB_SCHEMA}.cdp_segments s ON s.segment_id = %(segment_id)s AND s.tenant_id = %(tenant_id)s
        WHERE p.tenant_id = %(tenant_id)s
          AND p.status_code = 1
          AND s.segment_tag = ANY(p.segmentation_tags)
        """,
        {"segment_id": segment_id, "tenant_id": tenant_id},
    )
    row = cur.fetchone()
    return int(row["n"]) if row else 0


def activate_campaign(
    campaign_id: str,
    tenant_id: str,
    *,
    trigger_email: bool = True,
    log: Callable[[str], None] = logger.info,
) -> dict:
    """Validate + snapshot an Approved campaign, mark it Running, and submit the
    email_engine run. Returns a summary dict. See module docstring."""
    conn = connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            set_tenant_context(cur, tenant_id)

            campaign = _load_campaign(cur, tenant_id, campaign_id)
            if campaign is None:
                raise CampaignActivationError(f"campaign {campaign_id} not found for tenant {tenant_id}")
            if (campaign.get("approval_status") or "").strip() != "Approved":
                raise CampaignActivationError(
                    f"campaign {campaign_id} is not Approved (approval_status={campaign.get('approval_status')!r})"
                )
            if not campaign.get("template_id"):
                raise CampaignActivationError(f"campaign {campaign_id} has no template_id")
            if not campaign.get("segment_id"):
                raise CampaignActivationError(f"campaign {campaign_id} has no segment_id")

            template_status = _template_status(cur, tenant_id, str(campaign["template_id"]))
            if template_status != "Approved":
                raise CampaignActivationError(
                    f"template {campaign['template_id']} is not Approved (status={template_status!r})"
                )

            # Validate the segment row exists (mirror the template check) so a
            # dangling/deleted segment_id fails HERE, not one job away in email_engine.
            cur.execute(
                f"SELECT segment_tag FROM {DB_SCHEMA}.cdp_segments "
                f"WHERE segment_id = %(segment_id)s AND tenant_id = %(tenant_id)s",
                {"segment_id": str(campaign["segment_id"]), "tenant_id": tenant_id},
            )
            seg_row = cur.fetchone()
            if not seg_row or not seg_row.get("segment_tag"):
                raise CampaignActivationError(
                    f"segment {campaign['segment_id']} not found or has no segment_tag"
                )

            segment_size = _segment_snapshot_count(cur, tenant_id, str(campaign["segment_id"]))

            cur.execute(
                f"UPDATE {DB_SCHEMA}.crm_campaign SET status = 'Running' "
                f"WHERE campaign_id = %(campaign_id)s AND tenant_id = %(tenant_id)s",
                {"campaign_id": campaign_id, "tenant_id": tenant_id},
            )
        conn.commit()

        log(f"campaign_activation: campaign {campaign_id} Approved, segment size={segment_size} "
            f"(pre-eligibility; email_engine filters no-email / opt-out / suppressed)")

        summary = {
            "campaign_id": campaign_id,
            "tenant_id": tenant_id,
            "snapshot_count": segment_size,
            "email_engine_run_id": None,
        }
        if trigger_email:
            summary["email_engine_run_id"] = trigger_email_engine_job(campaign_id, tenant_id, log=log)
        return summary
    finally:
        conn.close()
