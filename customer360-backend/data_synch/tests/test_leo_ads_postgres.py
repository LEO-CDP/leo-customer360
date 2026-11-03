"""Opt-in integration tests. LEO_ADS_TEST_DSN must name a disposable database."""

import os
import uuid

import psycopg2
import pytest

import audience_sync
import check_leo_ads_readiness


pytestmark = pytest.mark.skipif(
    not os.environ.get("LEO_ADS_TEST_DSN"),
    reason="LEO_ADS_TEST_DSN must point to a disposable database with the promotions schema",
)


def test_membership_replacement_is_tenant_scoped_and_atomic(monkeypatch):
    dsn = os.environ["LEO_ADS_TEST_DSN"]
    monkeypatch.setenv("LEO_ADS_DATABASE_URL", dsn)
    monkeypatch.setattr(check_leo_ads_readiness, "_connect", lambda: psycopg2.connect(dsn))
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    campaign, segment = str(uuid.uuid4()), str(uuid.uuid4())
    profile_a, profile_b = str(uuid.uuid4()), str(uuid.uuid4())
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO leo_ads.tenant (tenant_key, name) VALUES (%s, %s), (%s, %s) RETURNING tenant_id",
            (tenant_a, "A", tenant_b, "B"),
        )
        ads_a, ads_b = [row[0] for row in cur.fetchall()]
        cur.execute(
            "INSERT INTO leo_ads.customer360_tenant_map (customer360_tenant_id, tenant_id) VALUES (%s, %s), (%s, %s)",
            (tenant_a, ads_a, tenant_b, ads_b),
        )
    assert check_leo_ads_readiness.check_readiness() is True

    def member(profile_id):
        return audience_sync.AudienceMember(profile_id, None, None)

    first = audience_sync._sync_leo_ads(
        tenant_a, campaign, segment, "A", [member(profile_a), member(profile_b)], 1
    )
    audience_sync._sync_leo_ads(tenant_b, campaign, segment, "B", [member(profile_a)], 1)
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg2.errors.ForeignKeyViolation):
            cur.execute("DELETE FROM leo_ads.customer360_tenant_map WHERE customer360_tenant_id = %s", (tenant_a,))
        conn.rollback()

    # A duplicate in the input fails after the audience upsert and must roll it back.
    with pytest.raises(psycopg2.IntegrityError):
        audience_sync._sync_leo_ads(
            tenant_a, campaign, segment, "Invalid", [member(profile_a), member(profile_a)], 1
        )
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT name, member_count_estimate FROM leo_ads.audience WHERE audience_id = %s", (first.audience_id,))
        assert cur.fetchone() == ("A", 2)

    audience_sync._sync_leo_ads(tenant_a, campaign, segment, "A", [member(profile_b)], 1)
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT tenant_id, count(*) FROM leo_ads.audience_member GROUP BY tenant_id ORDER BY tenant_id"
        )
        assert dict(cur.fetchall()) == {ads_a: 1, ads_b: 1}
        cur.execute("SELECT profile_ref FROM leo_ads.user_serving_key WHERE tenant_id = %s", (ads_a,))
        assert cur.fetchall() == [(profile_b,)]

    audience_sync._sync_leo_ads(tenant_a, campaign, segment, "A", [], 1)
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM leo_ads.audience_member WHERE tenant_id = %s", (ads_a,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM leo_ads.audience_member WHERE tenant_id = %s", (ads_b,))
        assert cur.fetchone()[0] == 1
    with pytest.raises(audience_sync.AudienceSyncError, match="tenant mapping"):
        audience_sync._sync_leo_ads(str(uuid.uuid4()), campaign, segment, "Unmapped", [], 1)


def test_campaign_sync_filters_consent_suppression_and_other_tenants(monkeypatch):
    dsn = os.environ["LEO_ADS_TEST_DSN"]
    monkeypatch.setenv("LEO_ADS_DATABASE_URL", dsn)
    monkeypatch.setattr(audience_sync, "_connect", lambda: psycopg2.connect(dsn))
    tenant, other_tenant, campaign, segment = [str(uuid.uuid4()) for _ in range(4)]
    allowed, denied, suppressed, other_profile = [str(uuid.uuid4()) for _ in range(4)]
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        for tenant_id in (tenant, other_tenant):
            cur.execute(
                """INSERT INTO customer360.sys_tenant
                   (tenant_id, tenant_code, tenant_name, company_name, business_type)
                   VALUES (%s, %s, 'Test', 'Test', 'retail')""",
                (tenant_id, tenant_id),
            )
        cur.execute(
            """INSERT INTO customer360.cdp_segments (segment_id, tenant_id, segment_name, segment_tag)
               VALUES (%s, %s, 'Segment', 'sync-test')""",
            (segment, tenant),
        )
        cur.execute(
            """INSERT INTO customer360.crm_campaign
               (campaign_id, tenant_id, segment_id, name, approval_status)
               VALUES (%s, %s, %s, 'Campaign', 'Approved')""",
            (campaign, tenant, segment),
        )
        for profile_id, owner, consent in (
            (allowed, tenant, True), (denied, tenant, False),
            (suppressed, tenant, True), (other_profile, other_tenant, True),
        ):
            cur.execute(
                """INSERT INTO customer360.cdp_master_profiles
                   (master_profile_id, tenant_id, segmentation_tags, communication_preferences)
                   VALUES (%s, %s, ARRAY['sync-test'], jsonb_build_object('leo_ads_opt_in', %s::boolean))""",
                (profile_id, owner, consent),
            )
        cur.execute(
            """INSERT INTO customer360.crm_suppression_list
               (tenant_id, channel, identifier_type, identifier, reason)
               VALUES (%s, 'GLOBAL', 'PROFILE_ID', %s, 'MANUAL')""",
            (tenant, suppressed),
        )
        cur.execute(
            "INSERT INTO leo_ads.tenant (tenant_key, name) VALUES (%s, 'Test') RETURNING tenant_id",
            (tenant,),
        )
        ads_tenant = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO leo_ads.customer360_tenant_map (customer360_tenant_id, tenant_id) VALUES (%s, %s)",
            (tenant, ads_tenant),
        )

    result = audience_sync.run_data_synch_job(
        tenant_id=tenant, campaign_id=campaign, segment_id=segment,
        platform="leo_ads", execution_id=str(uuid.uuid4()), batch_size=1,
    )
    assert (result["matched_count"], result["eligible_count"], result["uploaded_count"]) == (3, 1, 1)
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT profile_ref FROM leo_ads.user_serving_key WHERE tenant_id = %s", (ads_tenant,))
        assert cur.fetchall() == [(allowed,)]
        cur.execute(
            "UPDATE customer360.cdp_master_profiles SET communication_preferences = '{}' WHERE master_profile_id = %s",
            (allowed,),
        )
    result = audience_sync.run_data_synch_job(
        tenant_id=tenant, campaign_id=campaign, segment_id=segment,
        platform="leo_ads", execution_id=str(uuid.uuid4()), batch_size=1,
    )
    assert result["uploaded_count"] == 0
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM leo_ads.audience_member WHERE tenant_id = %s", (ads_tenant,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM leo_ads.user_serving_key WHERE tenant_id = %s", (ads_tenant,))
        assert cur.fetchone()[0] == 0


def test_removing_one_audience_preserves_another_audiences_serving_key(monkeypatch):
    dsn = os.environ["LEO_ADS_TEST_DSN"]
    monkeypatch.setenv("LEO_ADS_DATABASE_URL", dsn)
    tenant, segment, campaign_a, campaign_b, profile = [str(uuid.uuid4()) for _ in range(5)]
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO leo_ads.tenant (tenant_key, name) VALUES (%s, 'Overlap') RETURNING tenant_id",
            (tenant,),
        )
        ads_tenant = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO leo_ads.customer360_tenant_map (customer360_tenant_id, tenant_id) VALUES (%s, %s)",
            (tenant, ads_tenant),
        )
    member = audience_sync.AudienceMember(profile, None, None)
    audience_sync._sync_leo_ads(tenant, campaign_a, segment, "A", [member], 1)
    audience_sync._sync_leo_ads(tenant, campaign_b, segment, "B", [member], 1)
    audience_sync._sync_leo_ads(tenant, campaign_a, segment, "A", [], 1)
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM leo_ads.user_serving_key WHERE tenant_id = %s", (ads_tenant,))
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM leo_ads.audience_member WHERE tenant_id = %s", (ads_tenant,))
        assert cur.fetchone()[0] == 1


def test_sync_refuses_an_audience_key_owned_by_another_source(monkeypatch):
    dsn = os.environ["LEO_ADS_TEST_DSN"]
    monkeypatch.setenv("LEO_ADS_DATABASE_URL", dsn)
    tenant, campaign, segment = [str(uuid.uuid4()) for _ in range(3)]
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO leo_ads.tenant (tenant_key, name) VALUES (%s, 'Collision') RETURNING tenant_id",
            (tenant,),
        )
        ads_tenant = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO leo_ads.customer360_tenant_map (customer360_tenant_id, tenant_id) VALUES (%s, %s)",
            (tenant, ads_tenant),
        )
        cur.execute(
            "INSERT INTO leo_ads.audience (tenant_id, audience_key, name, provider) VALUES (%s, %s, 'Keep', 'manual')",
            (ads_tenant, f"customer360:{tenant}:{campaign}:{segment}"),
        )
    with pytest.raises(audience_sync.AudienceSyncError, match="owned by another source"):
        audience_sync._sync_leo_ads(tenant, campaign, segment, "Overwrite", [], 10)
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT name, provider FROM leo_ads.audience WHERE tenant_id = %s", (ads_tenant,))
        assert cur.fetchone() == ("Keep", "manual")
