"""Opt-in test using two distinct, disposable PostgreSQL databases."""

import os
import uuid

import psycopg2
import pytest

import audience_sync
import check_leo_ads_readiness


pytestmark = pytest.mark.skipif(
    not os.environ.get("LEO_ADS_TEST_SOURCE_DSN") or not os.environ.get("LEO_ADS_TEST_DSN"),
    reason="Set both LEO_ADS_TEST_SOURCE_DSN and LEO_ADS_TEST_DSN to disposable databases",
)


def test_sync_and_readiness_with_independent_databases(monkeypatch):
    source_dsn = os.environ["LEO_ADS_TEST_SOURCE_DSN"]
    target_dsn = os.environ["LEO_ADS_TEST_DSN"]
    monkeypatch.setattr(audience_sync, "_connect", lambda: psycopg2.connect(source_dsn))
    monkeypatch.setattr(check_leo_ads_readiness, "_connect", lambda: psycopg2.connect(source_dsn))
    monkeypatch.setenv("LEO_ADS_DATABASE_URL", target_dsn)
    tenant, campaign, segment, profile = [str(uuid.uuid4()) for _ in range(4)]

    with psycopg2.connect(source_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('leo_ads.audience')")
        assert cur.fetchone()[0] is None
        cur.execute(
            """INSERT INTO customer360.sys_tenant
               (tenant_id, tenant_code, tenant_name, company_name, business_type)
               VALUES (%s, %s, 'Test', 'Test', 'retail')""", (tenant, tenant)
        )
        cur.execute(
            """INSERT INTO customer360.cdp_segments (segment_id, tenant_id, segment_name, segment_tag)
               VALUES (%s, %s, 'Segment', 'separate-db-test')""", (segment, tenant)
        )
        cur.execute(
            """INSERT INTO customer360.crm_campaign (campaign_id, tenant_id, segment_id, name, approval_status)
               VALUES (%s, %s, %s, 'Campaign', 'Approved')""", (campaign, tenant, segment)
        )
        cur.execute(
            """INSERT INTO customer360.cdp_master_profiles
               (master_profile_id, tenant_id, segmentation_tags, communication_preferences)
               VALUES (%s, %s, ARRAY['separate-db-test'], '{"leo_ads_opt_in": true}')""",
            (profile, tenant),
        )

    with psycopg2.connect(target_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('customer360.crm_campaign')")
        assert cur.fetchone()[0] is None
        cur.execute(
            "INSERT INTO leo_ads.tenant (tenant_key, name) VALUES (%s, 'Test') RETURNING tenant_id",
            (tenant,),
        )
        target_tenant = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO leo_ads.customer360_tenant_map (customer360_tenant_id, tenant_id) VALUES (%s, %s)",
            (tenant, target_tenant),
        )

    assert check_leo_ads_readiness.check_readiness() is True
    result = audience_sync.run_data_synch_job(
        tenant_id=tenant, campaign_id=campaign, segment_id=segment,
        platform="leo_ads", execution_id=str(uuid.uuid4()), batch_size=1,
    )
    assert result["uploaded_count"] == 1
    with psycopg2.connect(target_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT profile_ref FROM leo_ads.user_serving_key WHERE tenant_id = %s", (target_tenant,))
        assert cur.fetchall() == [(profile,)]
    with psycopg2.connect(source_dsn) as conn, conn.cursor() as cur:
        cur.execute("UPDATE customer360.cdp_master_profiles SET communication_preferences = '{}' WHERE master_profile_id = %s", (profile,))

    result = audience_sync.run_data_synch_job(
        tenant_id=tenant, campaign_id=campaign, segment_id=segment,
        platform="leo_ads", execution_id=str(uuid.uuid4()), batch_size=1,
    )
    assert result["uploaded_count"] == 0
    with psycopg2.connect(target_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM leo_ads.audience_member WHERE tenant_id = %s", (target_tenant,))
        assert cur.fetchone()[0] == 0
