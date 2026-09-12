"""Campaign send pipeline.

``send_campaign`` is the real body behind ``email_engine_job``. Given one
Approved campaign + Approved template, it:

  1. validates the campaign is Approved and points at an Approved template +
     a segment (defense-in-depth; campaign_activation validated already),
  2. resolves the segment's members from ``cdp_master_profiles`` in keyset
     batches (segment_tag = ANY(segmentation_tags)),
  3. per recipient: skips the ineligible (no email / opted out) and the
     suppressed, renders the template, dispatches via the configured adapter,
  4. writes an idempotent ``cdp_campaign_dispatch_logs`` row per recipient
     (UNIQUE(campaign_id, master_profile_id)); a recipient already 'Sent' or
     'Suppressed' is never re-sent on replay.

Returns a per-status count summary. Each recipient runs inside its own SAVEPOINT
so one bad row never aborts the whole batch.
"""

import html
import logging
import os
from typing import Callable, Iterator, Optional

from psycopg2 import errors
from psycopg2.extras import RealDictCursor

from .adapters import DispatchAdapter, build_adapter
from .db import DB_SCHEMA, connect
from .provider_config import load_email_config
from .rendering import inject_tracking_pixel, render_string, rewrite_links_for_click_tracking
from .rls import set_tenant_context
from .tracking import encode_tracking_token

logger = logging.getLogger(__name__)

BATCH_SIZE = int(os.environ.get("EMAIL_ENGINE_BATCH_SIZE", "500"))
# Public base for the tracking endpoints, e.g.
# "https://track.example.com/c360api/api/v1". Empty -> tracking URLs omitted
# (mock runs / tests still send; they just carry no pixel/click wrapping).
PUBLIC_BASE_URL = os.environ.get("EMAIL_PUBLIC_BASE_URL", "").rstrip("/")

TERMINAL_STATUSES = ("Sent", "Suppressed")


class EmailEngineError(Exception):
    """Raised when a campaign cannot be executed (not Approved, no template,
    template not Approved, ...). Surfaces as a Dagster op failure."""


def _clean(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _display_name(profile: dict) -> str:
    first = _clean(profile.get("first_name")) or ""
    last = _clean(profile.get("last_name")) or ""
    return f"{first} {last}".strip()


def _is_opted_out(profile: dict) -> bool:
    """Only an explicit ``email_opt_in == false`` blocks a send; a missing flag
    is treated as eligible (many seeded/imported profiles carry no preference)."""
    prefs = profile.get("communication_preferences")
    return isinstance(prefs, dict) and prefs.get("email_opt_in") is False


def load_campaign(cur, tenant_id: str, campaign_id: str) -> Optional[dict]:
    cur.execute(
        f"""
        SELECT campaign_id, name, approval_status, status, segment_id, template_id
        FROM {DB_SCHEMA}.crm_campaign
        WHERE campaign_id = %(campaign_id)s AND tenant_id = %(tenant_id)s
        """,
        {"campaign_id": campaign_id, "tenant_id": tenant_id},
    )
    return cur.fetchone()


def load_template(cur, tenant_id: str, template_id: str) -> Optional[dict]:
    cur.execute(
        f"""
        SELECT template_id, subject, html_body, text_body, status
        FROM {DB_SCHEMA}.crm_email_templates
        WHERE template_id = %(template_id)s AND tenant_id = %(tenant_id)s
        """,
        {"template_id": template_id, "tenant_id": tenant_id},
    )
    return cur.fetchone()


def load_segment_tag(cur, tenant_id: str, segment_id: str) -> Optional[str]:
    cur.execute(
        f"""
        SELECT segment_tag FROM {DB_SCHEMA}.cdp_segments
        WHERE segment_id = %(segment_id)s AND tenant_id = %(tenant_id)s
        """,
        {"segment_id": segment_id, "tenant_id": tenant_id},
    )
    row = cur.fetchone()
    return row["segment_tag"] if row else None


def iter_recipients(conn, tenant_id: str, segment_tag: str, batch_size: int) -> Iterator[dict]:
    """Yield active profiles carrying ``segment_tag``, keyset-paginated by
    master_profile_id so a large segment never loads fully into memory."""
    last_id = "00000000-0000-0000-0000-000000000000"
    while True:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            set_tenant_context(cur, tenant_id)
            cur.execute(
                f"""
                SELECT master_profile_id, email, first_name, last_name, communication_preferences
                FROM {DB_SCHEMA}.cdp_master_profiles
                WHERE tenant_id = %(tenant_id)s
                  AND status_code = 1
                  AND %(segment_tag)s = ANY(segmentation_tags)
                  AND master_profile_id > CAST(%(last_id)s AS uuid)
                ORDER BY master_profile_id
                LIMIT %(batch_size)s
                """,
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


def _suppressed_emails(cur, tenant_id: str, emails: list) -> set:
    """Lower-cased emails on the compliance suppression list.

    Degrades gracefully to an empty set if the suppression table does not exist
    yet, so this pipeline runs before the suppression table exists."""
    wanted = [e.lower() for e in emails if e]
    if not wanted:
        return set()
    # Run inside a savepoint so a failure (e.g. suppression table absent)
    # can be rolled back without poisoning the surrounding batch transaction.
    cur.execute("SAVEPOINT suppression_lookup")
    try:
        cur.execute(
            f"SELECT lower(email) AS email FROM {DB_SCHEMA}.cdp_email_suppression "
            f"WHERE tenant_id = %(tenant_id)s AND lower(email) = ANY(%(emails)s)",
            {"tenant_id": tenant_id, "emails": wanted},
        )
        result = {row["email"] for row in cur.fetchall()}
        cur.execute("RELEASE SAVEPOINT suppression_lookup")
        return result
    except errors.UndefinedTable as exc:
        # Suppression table not deployed yet -> tolerate (nothing to suppress).
        cur.execute("ROLLBACK TO SAVEPOINT suppression_lookup")
        logger.warning("suppression table absent; treating none as suppressed: %s", exc)
        return set()
    except Exception:
        # Any other error (transient/timeout/deadlock): FAIL CLOSED -- do not send
        # a batch whose suppression state is unknown (compliance).
        cur.execute("ROLLBACK TO SAVEPOINT suppression_lookup")
        raise


def _current_status(cur, campaign_id: str, master_profile_id: str) -> Optional[str]:
    cur.execute(
        f"SELECT status FROM {DB_SCHEMA}.cdp_campaign_dispatch_logs "
        f"WHERE campaign_id = %(campaign_id)s AND master_profile_id = %(master_profile_id)s",
        {"campaign_id": campaign_id, "master_profile_id": master_profile_id},
    )
    row = cur.fetchone()
    return row["status"] if row else None


def _upsert_dispatch(cur, *, tenant_id, campaign_id, master_profile_id, template_id, recipient_email,
                     status, run_id, provider=None, provider_message_id=None,
                     rendered_subject=None, error_message=None) -> None:
    """Insert or update the recipient's dispatch row. The ON CONFLICT guard
    never downgrades a terminal ('Sent'/'Suppressed') row, so concurrent/replay
    runs stay idempotent."""
    cur.execute(
        f"""
        INSERT INTO {DB_SCHEMA}.cdp_campaign_dispatch_logs
            (tenant_id, campaign_id, master_profile_id, template_id, recipient_email,
             status, provider, provider_message_id, rendered_subject, error_message, run_id,
             attempt_count, dispatched_at)
        VALUES
            (%(tenant_id)s, %(campaign_id)s, %(master_profile_id)s, %(template_id)s, %(recipient_email)s,
             %(status)s, %(provider)s, %(provider_message_id)s, %(rendered_subject)s, %(error_message)s, %(run_id)s,
             CASE WHEN %(status)s IN ('Sent', 'Failed') THEN 1 ELSE 0 END,
             CASE WHEN %(status)s = 'Sent' THEN now() ELSE NULL END)
        ON CONFLICT (campaign_id, master_profile_id) DO UPDATE SET
            status = EXCLUDED.status,
            template_id = EXCLUDED.template_id,
            recipient_email = EXCLUDED.recipient_email,
            provider = EXCLUDED.provider,
            provider_message_id = EXCLUDED.provider_message_id,
            rendered_subject = EXCLUDED.rendered_subject,
            error_message = EXCLUDED.error_message,
            run_id = EXCLUDED.run_id,
            attempt_count = cdp_campaign_dispatch_logs.attempt_count
                + CASE WHEN EXCLUDED.status IN ('Sent', 'Failed') THEN 1 ELSE 0 END,
            dispatched_at = CASE WHEN EXCLUDED.status = 'Sent' THEN now()
                                 ELSE cdp_campaign_dispatch_logs.dispatched_at END,
            updated_at = now()
        WHERE cdp_campaign_dispatch_logs.status NOT IN ('Sent', 'Suppressed')
        """,
        {
            "tenant_id": tenant_id, "campaign_id": campaign_id, "master_profile_id": master_profile_id,
            "template_id": template_id, "recipient_email": recipient_email, "status": status,
            "provider": provider, "provider_message_id": provider_message_id,
            "rendered_subject": rendered_subject, "error_message": error_message, "run_id": run_id,
        },
    )


def _tracking_urls(tenant_id: str, campaign_id: str, master_profile_id: str) -> dict:
    """Build the open-pixel, click-base, and unsubscribe URLs for a recipient
    (empty when EMAIL_PUBLIC_BASE_URL is unset)."""
    if not PUBLIC_BASE_URL:
        return {"pixel": None, "click_base": None, "unsubscribe": ""}
    token = encode_tracking_token(tenant_id, campaign_id, master_profile_id)
    return {
        "pixel": f"{PUBLIC_BASE_URL}/track/email/open?u={token}",
        "click_base": f"{PUBLIC_BASE_URL}/track/email/click",
        "unsubscribe": f"{PUBLIC_BASE_URL}/track/email/unsubscribe?u={token}",
        "token": token,
    }


def _render_for_recipient(template: dict, profile: dict, urls: dict) -> dict:
    context = {
        "first_name": _clean(profile.get("first_name")) or "",
        "last_name": _clean(profile.get("last_name")) or "",
        "name": _display_name(profile),
        "email": _clean(profile.get("email")) or "",
        "unsubscribe_url": urls.get("unsubscribe", ""),
    }
    subject = render_string(template.get("subject"), context)
    # HTML-escape merge values for the HTML body so a profile field containing
    # markup can't inject into the email HTML (subject/text stay plain).
    html_context = {k: html.escape(str(v)) for k, v in context.items()}
    html_body = render_string(template.get("html_body"), html_context)
    text_body = render_string(template.get("text_body"), context)
    if urls.get("click_base") and urls.get("token"):
        html_body = rewrite_links_for_click_tracking(html_body, urls["click_base"], urls["token"])
    html_body = inject_tracking_pixel(html_body, urls.get("pixel"))
    return {"subject": subject, "html_body": html_body, "text_body": text_body}


def send_campaign(
    campaign_id: str,
    tenant_id: str,
    *,
    run_id: Optional[str] = None,
    adapter: Optional[DispatchAdapter] = None,
    batch_size: int = BATCH_SIZE,
    log: Callable[[str], None] = logger.info,
) -> dict:
    """Execute one Approved campaign's email send. See module docstring."""
    conn = connect()
    got_lock = False
    try:
        # H2: serialize dispatch per campaign (advisory-lock classid 1) so a
        # Dagster retry, re-activation, or concurrent run cannot double-SEND --
        # the ledger dedups rows, not the actual emails. If another run holds the
        # lock, skip this run rather than block/duplicate.
        with conn.cursor() as lock_cur:
            lock_cur.execute("SELECT pg_try_advisory_lock(1, hashtext(%s))", (str(campaign_id),))
            got_lock = bool(lock_cur.fetchone()[0])
        conn.commit()
        if not got_lock:
            log(f"email_engine: campaign {campaign_id} dispatch already in progress; skipping this run")
            return {
                "campaign_id": campaign_id, "tenant_id": tenant_id, "provider": None,
                "total": 0, "sent": 0, "failed": 0, "skipped": 0, "suppressed": 0,
                "already_sent": 0, "skipped_locked": True,
            }
        # Resolve the tenant's dispatch config (Redis -> DB -> env/mock) once per
        # run and build the adapter from it, unless a test injected one.
        if adapter is None:
            adapter = build_adapter(load_email_config(tenant_id, conn))
        summary = {
            "campaign_id": campaign_id, "tenant_id": tenant_id, "provider": adapter.provider_name,
            "total": 0, "sent": 0, "failed": 0, "skipped": 0, "suppressed": 0, "already_sent": 0,
        }

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            set_tenant_context(cur, tenant_id)
            campaign = load_campaign(cur, tenant_id, campaign_id)
            if campaign is None:
                raise EmailEngineError(f"campaign {campaign_id} not found for tenant {tenant_id}")
            if (campaign.get("approval_status") or "").strip() != "Approved":
                raise EmailEngineError(
                    f"campaign {campaign_id} is not Approved (approval_status={campaign.get('approval_status')!r})"
                )
            if not campaign.get("template_id"):
                raise EmailEngineError(f"campaign {campaign_id} has no template_id")
            if not campaign.get("segment_id"):
                raise EmailEngineError(f"campaign {campaign_id} has no segment_id")

            template = load_template(cur, tenant_id, str(campaign["template_id"]))
            if template is None:
                raise EmailEngineError(f"template {campaign['template_id']} not found")
            if (template.get("status") or "").strip() != "Approved":
                raise EmailEngineError(
                    f"template {campaign['template_id']} is not Approved (status={template.get('status')!r})"
                )

            segment_tag = load_segment_tag(cur, tenant_id, str(campaign["segment_id"]))
            if not segment_tag:
                raise EmailEngineError(f"segment {campaign['segment_id']} not found or has no segment_tag")

        template_id = str(campaign["template_id"])
        log(f"email_engine: sending campaign {campaign_id} (segment_tag={segment_tag}, provider={adapter.provider_name})")

        batch: list = []
        for profile in iter_recipients(conn, tenant_id, segment_tag, batch_size):
            batch.append(profile)
            if len(batch) >= batch_size:
                _process_batch(conn, adapter, tenant_id, campaign_id, template_id, template, batch, run_id, summary)
                batch = []
        if batch:
            _process_batch(conn, adapter, tenant_id, campaign_id, template_id, template, batch, run_id, summary)

        log(
            "email_engine: campaign %s done (total=%d sent=%d failed=%d skipped=%d suppressed=%d already_sent=%d)"
            % (campaign_id, summary["total"], summary["sent"], summary["failed"],
               summary["skipped"], summary["suppressed"], summary["already_sent"])
        )
        return summary
    finally:
        if got_lock:
            try:
                with conn.cursor() as lock_cur:
                    lock_cur.execute("SELECT pg_advisory_unlock(1, hashtext(%s))", (str(campaign_id),))
                conn.commit()
            except Exception:  # noqa: BLE001 - closing the connection releases it anyway.
                pass
        conn.close()


def _process_batch(conn, adapter, tenant_id, campaign_id, template_id, template, batch, run_id, summary) -> None:
    """Suppression-filter, render, dispatch, and ledger one batch of recipients.
    Each recipient is wrapped in a SAVEPOINT so a single failure is isolated."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        set_tenant_context(cur, tenant_id)
        suppressed = _suppressed_emails(cur, tenant_id, [p.get("email") for p in batch])

        for profile in batch:
            summary["total"] += 1
            master_profile_id = str(profile["master_profile_id"])
            email = _clean(profile.get("email"))
            cur.execute("SAVEPOINT recipient")
            try:
                existing = _current_status(cur, campaign_id, master_profile_id)
                if existing in TERMINAL_STATUSES:
                    summary["already_sent" if existing == "Sent" else "suppressed"] += 1
                    cur.execute("RELEASE SAVEPOINT recipient")
                    continue

                common = dict(
                    tenant_id=tenant_id, campaign_id=campaign_id, master_profile_id=master_profile_id,
                    template_id=template_id, recipient_email=email, run_id=run_id,
                )
                if email and email.lower() in suppressed:
                    _upsert_dispatch(cur, status="Suppressed", error_message="on suppression list", **common)
                    summary["suppressed"] += 1
                elif not email or _is_opted_out(profile):
                    reason = "no email address" if not email else "email_opt_in is false"
                    _upsert_dispatch(cur, status="Skipped", error_message=reason, **common)
                    summary["skipped"] += 1
                else:
                    urls = _tracking_urls(tenant_id, campaign_id, master_profile_id)
                    rendered = _render_for_recipient(template, profile, urls)
                    result = adapter.send(
                        to_email=email, subject=rendered["subject"],
                        html_body=rendered["html_body"], text_body=rendered["text_body"],
                    )
                    _upsert_dispatch(
                        cur, status="Sent" if result.ok else "Failed",
                        provider=adapter.provider_name, provider_message_id=result.provider_message_id,
                        rendered_subject=rendered["subject"], error_message=result.error, **common,
                    )
                    summary["sent" if result.ok else "failed"] += 1
                cur.execute("RELEASE SAVEPOINT recipient")
            except Exception as exc:  # noqa: BLE001 - isolate a bad recipient, keep the batch going.
                cur.execute("ROLLBACK TO SAVEPOINT recipient")
                summary["failed"] += 1
                logger.warning("email_engine: recipient %s failed: %s", master_profile_id, exc)
    conn.commit()
