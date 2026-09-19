"""Zalo ZNS campaign send pipeline (mirrors email_engine/send.py).

``send_zalo_campaign`` is the real body behind the notification_engine send job.
Given one Approved, ``channel='zalo_zns'`` campaign pointing at an Approved ZNS
template (a ``crm_message_templates`` row with metadata.channel='zalo_zns') + a
segment, it:

  1. validates approval/channel/template/segment (defense-in-depth),
  2. resolves the segment's members from ``cdp_master_profiles`` in keyset batches,
  3. per recipient: skips the ineligible (no phone / zalo_opt_in=false), binds the
     template's typed params, and dispatches via the ZNS adapter with an embedded
     tracking token (so the S3-first webhook can correlate the callback),
  4. writes an idempotent ``cdp_campaign_dispatch_logs`` row per recipient.

Opt-out state is NOT written here -- it is a projection materialized from S3 by
the opt-out projection op (Phase 3). Returns a per-status count summary.
"""

import logging
from typing import Callable, Iterator, Optional

from psycopg2.extras import RealDictCursor

from .adapters import DispatchAdapter, build_zns_adapter
from .config import BATCH_SIZE, DISPATCH_ADAPTER
from .db import DB_SCHEMA, connect
from .provider_config import load_oa_token
from .rendering import render_params
from .rls import set_tenant_context
from .tracking import encode_tracking_token

logger = logging.getLogger(__name__)
TERMINAL_STATUSES = ("Sent", "Suppressed")
ZALO_ZNS_CHANNEL = "zalo_zns"


class NotificationEngineError(Exception):
    """Raised when a Zalo campaign cannot be executed (not Approved, wrong
    channel, no ZNS template, ...). Surfaces as a Dagster op failure."""


def _clean(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _display_name(profile: dict) -> str:
    return f"{_clean(profile.get('first_name')) or ''} {_clean(profile.get('last_name')) or ''}".strip()


def _is_opted_out(profile: dict) -> bool:
    """Only an explicit ``zalo_opt_in == false`` blocks a send (missing = eligible)."""
    prefs = profile.get("communication_preferences")
    return isinstance(prefs, dict) and prefs.get("zalo_opt_in") is False


def load_campaign(cur, tenant_id: str, campaign_id: str) -> Optional[dict]:
    cur.execute(
        f"""SELECT campaign_id, name, approval_status, status, channel, segment_id,
                   template_id, ai_plan, metadata
              FROM {DB_SCHEMA}.crm_campaign
             WHERE campaign_id = %(campaign_id)s AND tenant_id = %(tenant_id)s""",
        {"campaign_id": campaign_id, "tenant_id": tenant_id},
    )
    return cur.fetchone()


def load_zns_template(cur, tenant_id: str, template_id: str) -> Optional[dict]:
    cur.execute(
        f"""SELECT template_id, name, status, variables, metadata
              FROM {DB_SCHEMA}.crm_message_templates
             WHERE template_id = %(template_id)s AND tenant_id = %(tenant_id)s""",
        {"template_id": template_id, "tenant_id": tenant_id},
    )
    return cur.fetchone()


def load_segment_tag(cur, tenant_id: str, segment_id: str) -> Optional[str]:
    cur.execute(
        f"""SELECT segment_tag FROM {DB_SCHEMA}.cdp_segments
             WHERE segment_id = %(segment_id)s AND tenant_id = %(tenant_id)s""",
        {"segment_id": segment_id, "tenant_id": tenant_id},
    )
    row = cur.fetchone()
    return row["segment_tag"] if row else None


def iter_recipients(conn, tenant_id: str, segment_tag: str, batch_size: int) -> Iterator[dict]:
    """Yield active profiles carrying ``segment_tag``, keyset-paginated by id."""
    last_id = "00000000-0000-0000-0000-000000000000"
    while True:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            set_tenant_context(cur, tenant_id)
            cur.execute(
                f"""SELECT master_profile_id, phone_number, first_name, last_name,
                           communication_preferences
                      FROM {DB_SCHEMA}.cdp_master_profiles
                     WHERE tenant_id = %(tenant_id)s
                       AND status_code = 1
                       AND %(segment_tag)s = ANY(segmentation_tags)
                       AND master_profile_id > CAST(%(last_id)s AS uuid)
                     ORDER BY master_profile_id
                     LIMIT %(batch_size)s""",
                {"tenant_id": tenant_id, "segment_tag": segment_tag, "last_id": last_id, "batch_size": batch_size},
            )
            rows = cur.fetchall()
        if not rows:
            return
        for row in rows:
            yield row
        last_id = str(rows[-1]["master_profile_id"])
        if len(rows) < batch_size:
            return


def _current_status(cur, campaign_id: str, master_profile_id: str) -> Optional[str]:
    cur.execute(
        f"SELECT status FROM {DB_SCHEMA}.cdp_campaign_dispatch_logs "
        f"WHERE campaign_id = %(campaign_id)s AND master_profile_id = %(master_profile_id)s",
        {"campaign_id": campaign_id, "master_profile_id": master_profile_id},
    )
    row = cur.fetchone()
    return row["status"] if row else None


def _upsert_dispatch(cur, *, tenant_id, campaign_id, master_profile_id, template_id, recipient,
                     status, run_id, provider=None, provider_message_id=None, error_message=None) -> None:
    """Insert/update the recipient's dispatch row; never downgrades a terminal
    ('Sent'/'Suppressed') row (idempotent replay). Same generic ledger the email
    channel uses -- ``recipient_email`` holds the phone for Zalo."""
    cur.execute(
        f"""INSERT INTO {DB_SCHEMA}.cdp_campaign_dispatch_logs
                (tenant_id, campaign_id, master_profile_id, template_id, recipient_email,
                 status, provider, provider_message_id, error_message, run_id,
                 attempt_count, dispatched_at)
            VALUES
                (%(tenant_id)s, %(campaign_id)s, %(master_profile_id)s, %(template_id)s, %(recipient)s,
                 %(status)s, %(provider)s, %(provider_message_id)s, %(error_message)s, %(run_id)s,
                 CASE WHEN %(status)s IN ('Sent', 'Failed') THEN 1 ELSE 0 END,
                 CASE WHEN %(status)s = 'Sent' THEN now() ELSE NULL END)
            ON CONFLICT (campaign_id, master_profile_id) DO UPDATE SET
                status = EXCLUDED.status,
                template_id = EXCLUDED.template_id,
                recipient_email = EXCLUDED.recipient_email,
                provider = EXCLUDED.provider,
                provider_message_id = EXCLUDED.provider_message_id,
                error_message = EXCLUDED.error_message,
                run_id = EXCLUDED.run_id,
                attempt_count = cdp_campaign_dispatch_logs.attempt_count
                    + CASE WHEN EXCLUDED.status IN ('Sent', 'Failed') THEN 1 ELSE 0 END,
                dispatched_at = CASE WHEN EXCLUDED.status = 'Sent' THEN now()
                                     ELSE cdp_campaign_dispatch_logs.dispatched_at END,
                updated_at = now()
            WHERE cdp_campaign_dispatch_logs.status NOT IN ('Sent', 'Suppressed')""",
        {"tenant_id": tenant_id, "campaign_id": campaign_id, "master_profile_id": master_profile_id,
         "template_id": template_id, "recipient": recipient, "status": status, "provider": provider,
         "provider_message_id": provider_message_id, "error_message": error_message, "run_id": run_id},
    )


def _base_template_data(campaign: dict) -> dict:
    """Campaign-level ZNS param values (filled by the AI draft / marketer)."""
    for source in (campaign.get("ai_plan"), campaign.get("metadata")):
        if isinstance(source, dict) and isinstance(source.get("template_data"), dict):
            return source["template_data"]
    return {}


def send_zalo_campaign(
    campaign_id: str,
    tenant_id: str,
    *,
    run_id: Optional[str] = None,
    adapter: Optional[DispatchAdapter] = None,
    batch_size: int = BATCH_SIZE,
    log: Callable[[str], None] = logger.info,
) -> dict:
    """Execute one Approved zalo_zns campaign's send. See module docstring."""
    conn = connect()
    got_lock = False
    try:
        with conn.cursor() as lock_cur:  # serialize dispatch per campaign (no double-send)
            lock_cur.execute("SELECT pg_try_advisory_lock(2, hashtext(%s))", (str(campaign_id),))
            got_lock = bool(lock_cur.fetchone()[0])
        conn.commit()
        if not got_lock:
            log(f"notification_engine: campaign {campaign_id} dispatch already in progress; skipping")
            return {"campaign_id": campaign_id, "tenant_id": tenant_id, "total": 0, "sent": 0,
                    "failed": 0, "skipped": 0, "already_sent": 0, "skipped_locked": True}

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            set_tenant_context(cur, tenant_id)
            campaign = load_campaign(cur, tenant_id, campaign_id)
            if campaign is None:
                raise NotificationEngineError(f"campaign {campaign_id} not found for tenant {tenant_id}")
            if (campaign.get("approval_status") or "").strip() != "Approved":
                raise NotificationEngineError(
                    f"campaign {campaign_id} is not Approved (approval_status={campaign.get('approval_status')!r})")
            if (campaign.get("channel") or "").strip() != ZALO_ZNS_CHANNEL:
                raise NotificationEngineError(
                    f"campaign {campaign_id} channel is not {ZALO_ZNS_CHANNEL} (channel={campaign.get('channel')!r})")
            if not campaign.get("template_id"):
                raise NotificationEngineError(f"campaign {campaign_id} has no template_id")
            if not campaign.get("segment_id"):
                raise NotificationEngineError(f"campaign {campaign_id} has no segment_id")

            template = load_zns_template(cur, tenant_id, str(campaign["template_id"]))
            if template is None:
                raise NotificationEngineError(f"template {campaign['template_id']} not found")
            if (template.get("status") or "").strip() != "Approved":
                raise NotificationEngineError(
                    f"template {campaign['template_id']} is not Approved (status={template.get('status')!r})")
            tpl_meta = template.get("metadata") or {}
            if tpl_meta.get("channel") != ZALO_ZNS_CHANNEL:
                raise NotificationEngineError(f"template {campaign['template_id']} is not a zalo_zns template")
            zns_template_id = tpl_meta.get("zalo_template_id")
            if not zns_template_id:
                raise NotificationEngineError(f"template {campaign['template_id']} has no zalo_template_id")

            segment_tag = load_segment_tag(cur, tenant_id, str(campaign["segment_id"]))
            if not segment_tag:
                raise NotificationEngineError(f"segment {campaign['segment_id']} not found or has no segment_tag")

        if adapter is None:
            token = load_oa_token(conn, tenant_id)
            access_token = token.get("access_token")
            # Fail loudly rather than silently falling back to the mock adapter when
            # real ZNS delivery is configured but the OA is not connected -- otherwise
            # a whole segment gets ledgered 'Sent' with mock ids and nothing delivered.
            if DISPATCH_ADAPTER.strip().lower() == "zns" and not access_token:
                raise NotificationEngineError(
                    f"campaign {campaign_id}: CRM_ZALO_DISPATCH_ADAPTER=zns but tenant "
                    f"{tenant_id} has no Zalo OA access token (OA not connected) -- refusing to send")
            adapter = build_zns_adapter(access_token)

        base_data = _base_template_data(campaign)
        # Guard: a zalo_zns campaign created outside the AI-draft flow (generic CRUD)
        # can reach here with no bound params -> Zalo rejects every recipient. Require
        # every param the template declares to be present in template_data first.
        required_params = [
            p if isinstance(p, str) else str((p or {}).get("name") or (p or {}).get("key") or "")
            for p in ((template.get("variables") or {}).get("params") or [])
        ]
        missing_params = [p for p in required_params if p and p not in base_data]
        if missing_params:
            raise NotificationEngineError(
                f"campaign {campaign_id}: template_data is missing required ZNS params "
                f"{missing_params} (campaign has no AI plan / bound params?)")
        crm_template_id = str(campaign["template_id"])
        summary = {"campaign_id": campaign_id, "tenant_id": tenant_id, "provider": adapter.provider_name,
                   "total": 0, "sent": 0, "failed": 0, "skipped": 0, "already_sent": 0}
        log(f"notification_engine: sending campaign {campaign_id} (segment_tag={segment_tag}, "
            f"zns_template={zns_template_id}, provider={adapter.provider_name})")

        batch: list = []
        for profile in iter_recipients(conn, tenant_id, segment_tag, batch_size):
            batch.append(profile)
            if len(batch) >= batch_size:
                _process_batch(conn, adapter, tenant_id, campaign_id, crm_template_id,
                               zns_template_id, base_data, batch, run_id, summary)
                batch = []
        if batch:
            _process_batch(conn, adapter, tenant_id, campaign_id, crm_template_id,
                           zns_template_id, base_data, batch, run_id, summary)

        log("notification_engine: campaign %s done (total=%d sent=%d failed=%d skipped=%d already_sent=%d)"
            % (campaign_id, summary["total"], summary["sent"], summary["failed"],
               summary["skipped"], summary["already_sent"]))
        return summary
    finally:
        if got_lock:
            try:
                with conn.cursor() as lock_cur:
                    lock_cur.execute("SELECT pg_advisory_unlock(2, hashtext(%s))", (str(campaign_id),))
                conn.commit()
            except Exception:  # noqa: BLE001 - closing the connection releases it anyway
                pass
        conn.close()


def _process_batch(conn, adapter, tenant_id, campaign_id, crm_template_id, zns_template_id,
                   base_data, batch, run_id, summary) -> None:
    """Render + dispatch + ledger one batch, one COMMITTED transaction per recipient.

    Intent ('Sending') is committed BEFORE the external ZNS send, so a committed
    'Sent' can never be rolled back by a later error, and a crash between send and
    result leaves a durable 'Sending' row. On replay a 'Sending' row is NOT re-sent
    (at-most-once -- the safe default for a paid channel: never double-bill); it is
    surfaced for operator / delivery-webhook reconciliation instead.
    ponytail: per-recipient commit over per-batch -- throughput ceiling accepted for
    correctness on money; the path to safe retry is a provider-side idempotency key.
    """
    for profile in batch:
        summary["total"] += 1
        master_profile_id = str(profile["master_profile_id"])
        phone = _clean(profile.get("phone_number"))
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                set_tenant_context(cur, tenant_id)
                existing = _current_status(cur, campaign_id, master_profile_id)
            conn.rollback()  # close the read-only tx before branching
            if existing in TERMINAL_STATUSES:
                summary["already_sent" if existing == "Sent" else "skipped"] += 1
                continue
            if existing == "Sending":
                # a prior run recorded intent but never confirmed -> do NOT resend
                # (avoid double-billing); leave it for reconciliation.
                summary["skipped"] += 1
                logger.warning("notification_engine: recipient %s left 'Sending' by a prior "
                               "run; not resent (reconcile via delivery callback)", master_profile_id)
                continue

            common = dict(tenant_id=tenant_id, campaign_id=campaign_id,
                          master_profile_id=master_profile_id, template_id=crm_template_id,
                          recipient=phone, run_id=run_id)
            if not phone or _is_opted_out(profile):
                reason = "no phone number" if not phone else "zalo_opt_in is false"
                with conn.cursor() as cur:
                    set_tenant_context(cur, tenant_id)
                    _upsert_dispatch(cur, status="Skipped", error_message=reason, **common)
                conn.commit()
                summary["skipped"] += 1
                continue

            # 1) record intent + commit BEFORE the external send
            with conn.cursor() as cur:
                set_tenant_context(cur, tenant_id)
                _upsert_dispatch(cur, status="Sending", **common)
            conn.commit()

            # 2) external, non-transactional ZNS send
            context = {
                "first_name": _clean(profile.get("first_name")) or "",
                "last_name": _clean(profile.get("last_name")) or "",
                "name": _display_name(profile),
                "phone": phone,
            }
            template_data = render_params(base_data, context)
            token = encode_tracking_token(tenant_id, campaign_id, master_profile_id)
            result = adapter.send(phone=phone, template_id=zns_template_id,
                                  template_data=template_data, tracking_id=token)

            # 3) record terminal result + commit
            with conn.cursor() as cur:
                set_tenant_context(cur, tenant_id)
                _upsert_dispatch(cur, status="Sent" if result.ok else "Failed",
                                 provider=adapter.provider_name,
                                 provider_message_id=result.provider_message_id,
                                 error_message=result.error, **common)
            conn.commit()
            summary["sent" if result.ok else "failed"] += 1
        except Exception as exc:  # noqa: BLE001 - isolate a bad recipient, keep the batch going
            conn.rollback()
            summary["failed"] += 1
            logger.warning("notification_engine: recipient %s failed: %s", master_profile_id, exc)
