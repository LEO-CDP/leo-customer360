#!/usr/bin/env python3
"""Seed an Approved template + Approved campaign so the execution pipeline can
be exercised without the AI template/campaign generators.

email_engine / campaign_activation only ever read Approved rows, so this script
inserts an Approved ``crm_email_templates`` row and an Approved ``crm_campaign``
linked to a segment + that template. Optionally it also tags a few sample
profiles with the segment's tag (and gives them an email) so the send has
recipients.

Usage (from backend-system/, DB_* env pointing at the target Postgres):

    TENANT_ID=<uuid> python scripts/seed_email_campaign.py
    TENANT_ID=<uuid> SEGMENT_ID=<uuid> TAG_PROFILES=5 python scripts/seed_email_campaign.py

Prints the campaign_id and the activate curl. Idempotent: re-running reuses the
same template/campaign (matched by name / campaign_code).
"""

import os
import sys
import uuid

import psycopg2
from psycopg2.extras import RealDictCursor

DB_SCHEMA = os.environ.get("DB_SCHEMA", "customer360")
TEMPLATE_NAME = os.environ.get("SEED_TEMPLATE_NAME", "Email Engine Seed Template")
CAMPAIGN_CODE = os.environ.get("SEED_CAMPAIGN_CODE", "SEED-EMAIL-05")

HTML_BODY = (
    "<html><body><p>Hi {{ first_name }},</p>"
    "<p>Thanks for being with us. <a href=\"https://example.com/offer\">See your offer</a>.</p>"
    "<p><a href=\"{{ unsubscribe_url }}\">Unsubscribe</a></p></body></html>"
)
TEXT_BODY = "Hi {{ first_name }},\nThanks for being with us. Unsubscribe: {{ unsubscribe_url }}"


def _connect():
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        dbname=os.environ.get("DB_NAME", "customer360"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", "postgres"),
        port=os.environ.get("DB_PORT", "5432"),
    )


def _resolve_segment(cur, tenant_id, segment_id):
    if segment_id:
        cur.execute(
            f"SELECT segment_id, segment_tag FROM {DB_SCHEMA}.cdp_segments "
            f"WHERE segment_id = %s AND tenant_id = %s",
            (segment_id, tenant_id),
        )
    else:
        cur.execute(
            f"SELECT segment_id, segment_tag FROM {DB_SCHEMA}.cdp_segments "
            f"WHERE tenant_id = %s AND is_active = TRUE ORDER BY created_at LIMIT 1",
            (tenant_id,),
        )
    row = cur.fetchone()
    if not row:
        sys.exit("No segment found: create one (or pass SEGMENT_ID) before seeding.")
    return row["segment_id"], row["segment_tag"]


def _upsert_template(cur, tenant_id):
    cur.execute(
        f"""
        INSERT INTO {DB_SCHEMA}.crm_email_templates
            (tenant_id, name, subject, html_body, text_body, variables, status, approved_at)
        VALUES (%s, %s, %s, %s, %s, %s, 'Approved', now())
        ON CONFLICT DO NOTHING
        RETURNING template_id
        """,
        (tenant_id, TEMPLATE_NAME, "A little something for you, {{ first_name }}",
         HTML_BODY, TEXT_BODY, '{"first_name": "string", "unsubscribe_url": "string"}'),
    )
    row = cur.fetchone()
    if row:
        return row["template_id"]
    cur.execute(
        f"SELECT template_id FROM {DB_SCHEMA}.crm_email_templates "
        f"WHERE tenant_id = %s AND name = %s",
        (tenant_id, TEMPLATE_NAME),
    )
    return cur.fetchone()["template_id"]


def _upsert_campaign(cur, tenant_id, segment_id, template_id):
    cur.execute(
        f"""
        INSERT INTO {DB_SCHEMA}.crm_campaign
            (tenant_id, campaign_code, name, status, channel, objective,
             segment_id, template_id, approval_status, approved_at)
        VALUES (%s, %s, %s, 'Approved', 'email', 'Engagement', %s, %s, 'Approved', now())
        ON CONFLICT (tenant_id, campaign_code) DO UPDATE SET
            segment_id = EXCLUDED.segment_id,
            template_id = EXCLUDED.template_id,
            approval_status = 'Approved'
        RETURNING campaign_id
        """,
        (tenant_id, CAMPAIGN_CODE, "Email Engine Seed Campaign", segment_id, template_id),
    )
    return cur.fetchone()["campaign_id"]


def _tag_sample_profiles(cur, tenant_id, segment_tag, count):
    """Give a few active profiles the segment tag + a demo email so the send
    has eligible recipients. Only touches profiles lacking the tag."""
    cur.execute(
        f"""
        UPDATE {DB_SCHEMA}.cdp_master_profiles
        SET segmentation_tags = array_append(coalesce(segmentation_tags, '{{}}'), %s),
            email = coalesce(email, 'seed+' || left(master_profile_id::text, 8) || '@example.com')
        WHERE master_profile_id IN (
            SELECT master_profile_id FROM {DB_SCHEMA}.cdp_master_profiles
            WHERE tenant_id = %s AND status_code = 1
              AND NOT (%s = ANY(coalesce(segmentation_tags, '{{}}')))
            LIMIT %s
        )
        """,
        (segment_tag, tenant_id, segment_tag, count),
    )
    return cur.rowcount


def main():
    tenant_id = os.environ.get("TENANT_ID")
    if not tenant_id:
        sys.exit("TENANT_ID env is required.")
    segment_id = os.environ.get("SEGMENT_ID")
    tag_count = int(os.environ.get("TAG_PROFILES", "0"))

    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SET app.tenant_id = %s", (tenant_id,))
            segment_id, segment_tag = _resolve_segment(cur, tenant_id, segment_id)
            template_id = _upsert_template(cur, tenant_id)
            campaign_id = _upsert_campaign(cur, tenant_id, segment_id, template_id)
            tagged = _tag_sample_profiles(cur, tenant_id, segment_tag, tag_count) if tag_count else 0
        conn.commit()
    finally:
        conn.close()

    print(f"tenant_id   = {tenant_id}")
    print(f"segment_id  = {segment_id} (tag={segment_tag})")
    print(f"template_id = {template_id} (Approved)")
    print(f"campaign_id = {campaign_id} (Approved)")
    if tag_count:
        print(f"tagged {tagged} sample profile(s) with '{segment_tag}'")
    print()
    print("Activate via API:")
    print(f"  POST /api/v1/admin/campaigns/{campaign_id}/activate")


if __name__ == "__main__":
    main()
