"""Seeds comprehensive demo data covering every table/column in
core-customer360/database-schema.sql that ``init_sample_data.py`` +
``run_demo_resolution.py`` do NOT already exercise.

Those two scripts only cover the Customer Identity Resolution (CIR) slice:
AppsFlyer raw-profile ingestion -> resolved ``cdp_master_profiles`` rows. This
script MUST run AFTER ``run_demo_resolution.py`` (see ``run-demo.sh``) so it
can enrich the already-resolved master profiles and link new demo rows to
real ``master_profile_id`` values. It covers:

1. CRM Journey Graph: ``crm_industry``, ``crm_account``, ``crm_lead_source``,
   ``crm_lead``, ``crm_campaign``, ``crm_campaign_member``, ``crm_contact``,
   ``crm_opportunity`` -- the Lead -> CampaignMember -> Contact -> Opportunity
   B2B journey described in CIR-Tech-Slides-VN.md / README.md.
2. Relations: ``cdp_relation_types`` (friend/colleague/family/customer-contact)
   + ``cdp_relations`` linking real resolved master profiles together.
3. ``crm_customer_contacts`` (CS/call-center/email interaction log) and
   ``crm_transactions`` (retail purchases, banking transfers/payments --
   including a couple of NOT-YET-identity-resolved rows with
   ``master_profile_id = NULL``, the same async-backfill pattern used by
   ``cdp_raw_events``).
4. ``cdp_raw_events``: sample behavioral events spanning every
   ``event_category`` seeded in ``cdp_event_catalog`` (GENERAL/FEEDBACK/
   COMMERCE/FINANCE/STOCK_TRADING/TRAVEL/REAL_ESTATE), including a few
   travel/real_estate events with NO master profile yet (domains not
   otherwise represented among the AppsFlyer-only CIR demo profiles).
5. ``graph_edges``: a handful of edges spanning several relation partitions
   (``belongs_to``, ``converted``, ``has``, ``belongs_to_industry``,
   ``is_connected_to``, ``is_from``).
6. ``cdp_master_profiles`` enrichment: fills in every column NOT already set
   by ``CustomerIdentityResolver`` -- lifecycle/engagement tracking
   (customer_since/last_activity_at/preferred_channel/lifecycle_stage/
   persona_summary), the full ML scoring block (lead/churn/CLV/CX/data
   quality), retail-only attrs (loyalty_id/membership_tier/
   preferred_store_code) for retail-domain profiles, banking-only attrs
   (cif_number/account_numbers/kyc_status/risk_segment) for banking-domain
   profiles, acquisition_source/acquisition_campaign (joined back from the
   raw profile that first created the master, via first_seen_raw_profile_id),
   segmentation_tags/attributes/gender/address/profile_picture_url, and a
   ``persona_embedding`` vector for a representative subset of profiles.
7. **crm_contact <-> cdp_master_profiles linkage**: these two tables have NO
   shared key in database-schema.sql (crm_contact has no tenant_id/
   master_profile_id column, and cdp_master_profiles has nothing pointing
   back to crm_contact) -- they represent separate B2B-CRM vs B2C-identity-
   resolution domains. ``link_crm_contacts_to_master_profiles()`` bridges a
   handful of them via the generic ``graph_edges`` table
   (``relation = 'is_active_as'``, ``cdp_master_profiles -> crm_contact``),
   PLUS a denormalized cross-reference id on each side
   (``cdp_master_profiles.attributes->>'linked_crm_contact_id'`` and
   ``crm_contact.metadata->>'linked_master_profile_id'``) so the link is
   discoverable/joinable from either table without necessarily touching
   graph_edges.

Deliberately NOT populated (left NULL / default), consistent with this
demo's existing "never store plaintext PII" policy for identity-resolution
tables (see init_sample_data.py's hash_pii()): ``first_name``/``last_name``
(no plaintext name is available -- full_name is a one-way hash),
``secondary_emails``/``secondary_phones``, and ``date_of_birth``. ``address``
is populated with city/country only (no street). ``gender`` and
``profile_picture_url`` ARE populated -- neither is independently
identifying PII.

Note: ``crm_lead``/``crm_contact`` DO get plaintext first/last name/email/
phone -- that's a *different* table representing a separate use case (a
Salesforce-style B2B CRM record), not the hashed-PII identity-resolution
pipeline, and the schema itself defines those columns as plain TEXT with no
hashing expectation. Names used are obviously-synthetic demo placeholders.

Idempotent / safe to re-run:
- CRM entities (crm_industry/crm_account/.../crm_opportunity) have NO
  tenant_id column in database-schema.sql, so they're keyed by deterministic
  uuid5 ids (derived from a fixed demo string) and upserted via
  ``ON CONFLICT (pk) DO UPDATE``.
- ``cdp_relation_types`` is upserted via ``ON CONFLICT (code) DO NOTHING``.
- Every tenant-scoped table seeded here (cdp_relations, crm_customer_contacts,
  crm_transactions, cdp_raw_events) is reset (``DELETE ... WHERE tenant_id =
  DEMO_TENANT_ID``) before reinserting.
- ``graph_edges`` has no tenant_id either -- demo rows are tagged
  ``metadata->>'demo_tenant' = DEMO_TENANT_ID`` and reset via that filter.
- ``cdp_master_profiles`` enrichment is a plain UPDATE keyed by
  ``master_profile_id`` -- naturally idempotent (re-running just recomputes
  the same deterministic values, since every generator below is seeded from
  the profile's own id).
- ``link_crm_contacts_to_master_profiles()``'s ``graph_edges`` rows are covered
  by the same ``reset_tenant_scoped_demo_tables()`` delete (tagged
  ``metadata->>'demo_tenant'``) as ``seed_graph_edges()``, so re-running never
  duplicates them; the ``attributes``/``metadata`` cross-reference UPDATEs are
  independently idempotent too (same key -> same value every time via jsonb
  ``||`` merge).
"""

import hashlib
import logging
import os
import random
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import Json, RealDictCursor

# Make the identity_resolution package importable when this script is run
# directly (python scripts/seed_full_demo_data.py) rather than as a module --
# needed to reuse the real PersonaResolutionEngine (persona_engine.py) below
# instead of re-implementing its SQL inline.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from identity_resolution.persona_engine import PersonaResolutionEngine  # noqa: E402

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_NAME = os.environ.get("DB_NAME", "customer360")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "password")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_SCHEMA = os.environ.get("DB_SCHEMA", "customer360")

# Must match scripts/init_sample_data.py / scripts/run_demo_resolution.py.
DEMO_TENANT_ID = "11111111-1111-1111-1111-111111111111"

# Fixed namespace so every "demo:<key>" -> deterministic UUID, making the
# tenant-less CRM entity tables safe to re-seed without ever duplicating rows.
DEMO_NAMESPACE = uuid.UUID("12345678-1234-5678-1234-567812345678")

# How many resolved master profiles get the heavier per-row demo content
# (customer contacts / transactions / raw events / persona_embedding). All
# master profiles still get the lightweight lifecycle+scoring enrichment.
DETAIL_PROFILE_LIMIT = 60
EMBEDDING_PROFILE_LIMIT = 30
PERSONA_EMBEDDING_DIM = 768
# Demo invariant: every resolved master profile must have >10 behavioral events.
MIN_EVENTS_PER_MASTER_PROFILE = 11


def _table(name: str) -> str:
    return f"{DB_SCHEMA}.{name}" if DB_SCHEMA else name


def demo_id(key: str) -> str:
    """Deterministic UUID for a given demo entity key -- makes tenant-less
    CRM tables safe to re-seed (same key always -> same primary key)."""
    return str(uuid.uuid5(DEMO_NAMESPACE, key))


def stable_rng(key: str) -> random.Random:
    """A random.Random seeded deterministically from ``key`` (e.g. a
    master_profile_id) -- keeps this whole script idempotent."""
    seed = int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16) % (2**32)
    return random.Random(seed)


# --------------------------------------------------------------------------
# 1. CRM Journey Graph
# --------------------------------------------------------------------------

INDUSTRIES = [
    ("Banking & Financial Services", "Retail and commercial banking, wealth management."),
    ("Retail & E-commerce", "Omni-channel retail, marketplaces and D2C brands."),
    ("Real Estate", "Residential and commercial property developers/agencies."),
    ("Travel & Hospitality", "Airlines, OTAs, hotel groups and tour operators."),
]

ACCOUNTS = [
    ("Sacombank Digital", "Banking & Financial Services"),
    ("Techcombank Wealth Partners", "Banking & Financial Services"),
    ("VinMart Retail Group", "Retail & E-commerce"),
    ("Saigon Co.op Omnichannel", "Retail & E-commerce"),
    ("Danh Khoi Real Estate", "Real Estate"),
    ("Vietravel Holdings", "Travel & Hospitality"),
]

LEAD_SOURCES = [
    ("Website Contact Form", "Inbound leads from the corporate website."),
    ("Trade Show", "Leads captured at industry conferences/booths."),
    ("Referral Partner", "Leads referred by an existing customer or partner."),
    ("Cold Outreach", "Outbound SDR prospecting (email/call)."),
    ("Paid Search", "Leads from Google/Bing search ads."),
]

# (name, campaign_code, status, channel, platform, objective, budget_vnd, start_offset, end_offset, utm_source, utm_medium)
CAMPAIGNS = [
    (
        "Q4 Banking App Install - Google UAC",
        "BANK-Q4-GOOG-UAC-001",
        "Active",
        "Paid Search",
        "Google",
        "App Install",
        500_000_000,
        -60, 30,
        "google", "cpc",
    ),
    (
        "Retail Mega Sale - Meta Retargeting",
        "RETAIL-MEGA-META-002",
        "Active",
        "Paid Social",
        "Meta",
        "Conversions",
        320_000_000,
        -45, 15,
        "meta", "paid_social",
    ),
    (
        "Real Estate Awareness - TikTok",
        "RE-AWARE-TIKTOK-003",
        "Active",
        "Paid Social",
        "TikTok",
        "Awareness",
        180_000_000,
        -30, 60,
        "tiktok", "paid_social",
    ),
    (
        "Travel Q1 Leads - Zalo Ads",
        "TRAVEL-Q1-ZALO-004",
        "Paused",
        "Paid Social",
        "Zalo",
        "Leads",
        150_000_000,
        -90, -10,
        "zalo", "paid_social",
    ),
    (
        "Banking CLV Push - AppsFlyer Retargeting",
        "BANK-CLV-AF-005",
        "Active",
        "Push Notification",
        "AppsFlyer",
        "Retention",
        200_000_000,
        -20, 40,
        "appsflyer", "push",
    ),
    (
        "Retail Email Re-engagement",
        "RETAIL-EMAIL-006",
        "Completed",
        "Email",
        "Google",
        "Conversions",
        80_000_000,
        -120, -30,
        "email", "email",
    ),
    (
        "Real Estate YouTube Brand - GA4 Tracked",
        "RE-YT-BRAND-007",
        "Active",
        "Video",
        "YouTube",
        "Awareness",
        250_000_000,
        -15, 75,
        "youtube", "video",
    ),
    (
        "Travel Recovery - Google Performance Max",
        "TRAVEL-PMAX-008",
        "Draft",
        "Paid Search",
        "Google",
        "Conversions",
        400_000_000,
        5, 90,
        "google", "pmax",
    ),
]

LEAD_FIRST_NAMES = ("Minh", "Linh", "Huy", "Trang", "Khoa", "My", "Duc", "Anh")
LEAD_LAST_NAMES = ("Nguyen", "Tran", "Le", "Pham", "Hoang", "Vo", "Bui", "Dang")


def seed_relation_types(cursor) -> None:
    logger.info("Seeding cdp_relation_types...")
    for code, description in (
        ("friend", "Personal friendship between two profiles."),
        ("colleague", "Coworker relationship between two profiles."),
        ("family", "Family/household relationship between two profiles."),
        ("customer-contact", "One profile referred or is a point of contact for another."),
    ):
        cursor.execute(
            f"""
            INSERT INTO {_table('cdp_relation_types')} (code, description)
            VALUES (%s, %s)
            ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;
            """,
            (code, description),
        )


def seed_crm_entities(cursor) -> dict:
    """Seeds the CRM journey graph and returns a dict of the demo entity ids
    keyed by kind (for cross-referencing from graph_edges)."""
    logger.info("Seeding CRM journey graph (industries/accounts/lead sources/leads/campaigns/...)...")
    ids: dict = {"industry": {}, "account": {}, "lead_source": {}, "lead": [], "campaign": {}, "contact": [], "opportunity": []}

    for name, description in INDUSTRIES:
        industry_id = demo_id(f"crm_industry:{name}")
        cursor.execute(
            f"""
            INSERT INTO {_table('crm_industry')} (industry_id, tenant_id, name, description, keywords)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (industry_id) DO UPDATE SET description = EXCLUDED.description;
            """,
            (industry_id, DEMO_TENANT_ID, name, description, [name.lower().replace(" & ", "_").replace(" ", "_")]),
        )
        ids["industry"][name] = industry_id

    for name, industry_name in ACCOUNTS:
        account_id = demo_id(f"crm_account:{name}")
        cursor.execute(
            f"""
            INSERT INTO {_table('crm_account')} (account_id, tenant_id, name, industry_id, description, keywords)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (account_id) DO UPDATE SET industry_id = EXCLUDED.industry_id;
            """,
            (account_id, DEMO_TENANT_ID, name, ids["industry"][industry_name], f"Demo account in {industry_name}.", [industry_name]),
        )
        ids["account"][name] = account_id

    for name, description in LEAD_SOURCES:
        lead_source_id = demo_id(f"crm_lead_source:{name}")
        cursor.execute(
            f"""
            INSERT INTO {_table('crm_lead_source')} (lead_source_id, tenant_id, name, description)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (lead_source_id) DO UPDATE SET description = EXCLUDED.description;
            """,
            (lead_source_id, DEMO_TENANT_ID, name, description),
        )
        ids["lead_source"][name] = lead_source_id

    for (name, campaign_code, status, channel, platform, objective,
         budget_vnd, start_offset, end_offset, utm_source, utm_medium) in CAMPAIGNS:
        campaign_id = demo_id(f"crm_campaign:{name}")
        today = datetime.now().date()
        cursor.execute(
            f"""
            INSERT INTO {_table('crm_campaign')}
                (campaign_id, tenant_id, campaign_code, name, status, channel, platform,
                 objective, description, keywords, start_date, end_date,
                 budget_amount, currency, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (campaign_id) DO UPDATE SET
                status = EXCLUDED.status,
                start_date = EXCLUDED.start_date,
                end_date = EXCLUDED.end_date,
                budget_amount = EXCLUDED.budget_amount;
            """,
            (
                campaign_id, DEMO_TENANT_ID, campaign_code, name, status,
                channel, platform, objective,
                f"Demo {channel} campaign on {platform} targeting {objective}.",
                [channel.lower().replace(" ", "_"), platform.lower(), objective.lower().replace(" ", "_")],
                today + timedelta(days=start_offset),
                today + timedelta(days=end_offset),
                budget_vnd, "VND",
                Json({
                    "utm_source": utm_source,
                    "utm_medium": utm_medium,
                    "utm_campaign": campaign_code.lower(),
                    "utm_content": f"{platform.lower()}-{objective.lower().replace(' ', '_')}",
                    "tracking_platform": "appsflyer" if platform == "AppsFlyer" else "ga4",
                }),
            ),
        )
        ids["campaign"][name] = campaign_id

    rng = stable_rng("crm_leads")
    lead_source_names = list(ids["lead_source"].keys())
    for i in range(8):
        lead_id = demo_id(f"crm_lead:{i}")
        first_name = rng.choice(LEAD_FIRST_NAMES)
        last_name = rng.choice(LEAD_LAST_NAMES)
        source_name = rng.choice(lead_source_names)
        cursor.execute(
            f"""
            INSERT INTO {_table('crm_lead')}
                (lead_id, tenant_id, first_name, last_name, email, phone, description, keywords, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (lead_id) DO UPDATE SET description = EXCLUDED.description;
            """,
            (
                lead_id, DEMO_TENANT_ID, first_name, last_name,
                f"demo.lead{i}@example.com", f"09{rng.randint(10000000, 99999999)}",
                f"Synthetic demo lead sourced via {source_name}.", [source_name],
                Json({"lead_source": source_name, "synthetic": True}),
            ),
        )
        ids["lead"].append(lead_id)

    contact_defs = [
        ("Sacombank Digital", 0), ("Techcombank Wealth Partners", 1),
        ("VinMart Retail Group", 2), ("Saigon Co.op Omnichannel", 3),
        ("Danh Khoi Real Estate", 4), ("Vietravel Holdings", 5),
    ]
    for account_name, lead_index in contact_defs:
        contact_id = demo_id(f"crm_contact:{account_name}")
        first_name = rng.choice(LEAD_FIRST_NAMES)
        last_name = rng.choice(LEAD_LAST_NAMES)
        cursor.execute(
            f"""
            INSERT INTO {_table('crm_contact')}
                (contact_id, tenant_id, first_name, last_name, email, phone, account_id, description, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (contact_id) DO UPDATE SET account_id = EXCLUDED.account_id;
            """,
            (
                contact_id, DEMO_TENANT_ID, first_name, last_name,
                f"{first_name.lower()}.{last_name.lower()}@{account_name.lower().split()[0]}.example.com",
                f"09{rng.randint(10000000, 99999999)}", ids["account"][account_name],
                f"Primary contact at {account_name}, converted from a demo lead.",
                Json({"converted_from_lead_id": ids["lead"][lead_index]}),
            ),
        )
        ids["contact"].append(contact_id)

    opportunity_defs = [
        ("Sacombank Digital", "Digital Banking Platform Renewal", 1_200_000_000, "negotiation", 45),
        ("VinMart Retail Group", "Loyalty Program Expansion", 350_000_000, "proposal", 30),
        ("Danh Khoi Real Estate", "CRM + Customer 360 Rollout", 800_000_000, "qualification", 90),
        ("Vietravel Holdings", "Booking Personalization Engine", 600_000_000, "closed_won", -10),
    ]
    for account_name, opp_name, value, stage, close_offset in opportunity_defs:
        opportunity_id = demo_id(f"crm_opportunity:{opp_name}")
        cursor.execute(
            f"""
            INSERT INTO {_table('crm_opportunity')}
                (opportunity_id, tenant_id, account_id, name, value, stage, close_date, description)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (opportunity_id) DO UPDATE SET stage = EXCLUDED.stage, value = EXCLUDED.value;
            """,
            (
                opportunity_id, DEMO_TENANT_ID, ids["account"][account_name], opp_name, value, stage,
                datetime.now().date() + timedelta(days=close_offset),
                f"Demo opportunity with {account_name}.",
            ),
        )
        ids["opportunity"].append(opportunity_id)

    # Campaign members: attach contacts and leads to the first 3 active/completed campaigns.
    active_campaign_names = [
        name for (name, _code, status, *_rest) in CAMPAIGNS if status in ("Active", "Completed")
    ][:3]
    for i, contact_id in enumerate(ids["contact"]):
        cursor.execute(
            f"""
            INSERT INTO {_table('crm_campaign_member')}
                (campaign_member_id, tenant_id, campaign_id, contact_id, status, description)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (campaign_member_id) DO UPDATE SET status = EXCLUDED.status;
            """,
            (
                demo_id(f"crm_campaign_member:contact:{contact_id}"), DEMO_TENANT_ID,
                ids["campaign"][active_campaign_names[i % len(active_campaign_names)]],
                contact_id, "converted", "Already-converted contact who engaged with this campaign.",
            ),
        )
    for i, lead_id in enumerate(ids["lead"][:4]):
        cursor.execute(
            f"""
            INSERT INTO {_table('crm_campaign_member')}
                (campaign_member_id, tenant_id, campaign_id, contact_id, status, description, metadata)
            VALUES (%s, %s, %s, NULL, %s, %s, %s)
            ON CONFLICT (campaign_member_id) DO UPDATE SET status = EXCLUDED.status;
            """,
            (
                demo_id(f"crm_campaign_member:lead:{lead_id}"), DEMO_TENANT_ID,
                ids["campaign"][active_campaign_names[i % len(active_campaign_names)]],
                "responded", "Lead responded to campaign but has not converted to a Contact yet.",
                Json({"lead_id": lead_id}),
            ),
        )

    return ids


# --------------------------------------------------------------------------
# 2-4. Relations, interactions, transactions, behavioral events
# --------------------------------------------------------------------------

# Platform-specific realistic metric profiles for daily performance seeding.
# (impressions_range, clicks_pct, conversions_pct, revenue_per_conversion_vnd)
_PLATFORM_PROFILE = {
    "Google":     ((8_000, 40_000), 0.045, 0.08, 1_200_000),
    "Meta":       ((15_000, 60_000), 0.018, 0.05, 900_000),
    "TikTok":     ((20_000, 80_000), 0.012, 0.03, 600_000),
    "Zalo":       ((5_000, 25_000), 0.025, 0.06, 800_000),
    "AppsFlyer":  ((3_000, 15_000), 0.060, 0.15, 500_000),  # re-targeting: higher CVR
    "YouTube":    ((30_000, 120_000), 0.005, 0.015, 1_500_000),
}
_DEFAULT_PROFILE = ((5_000, 20_000), 0.03, 0.05, 700_000)


def seed_campaign_performance_daily(cursor, campaign_ids: dict) -> None:
    """Inserts daily performance rows for each seeded campaign.

    Metrics are generated with a per-platform profile and a deterministic RNG
    so the script is fully idempotent.  Only days where the campaign was
    already running (start_date <= today) are inserted.
    """
    logger.info("Seeding crm_campaign_performance_daily with AppsFlyer/GA4-style metrics...")
    today = datetime.now().date()

    for (name, campaign_code, status, channel, platform, objective,
         budget_vnd, start_offset, end_offset, utm_source, utm_medium) in CAMPAIGNS:
        campaign_id = campaign_ids.get(name)
        if campaign_id is None:
            continue

        run_start = today + timedelta(days=start_offset)
        run_end = min(today, today + timedelta(days=end_offset))  # don't seed future days
        if run_start > today:
            continue  # Draft / future campaign — no daily data yet

        imp_range, ctr, cvr, rev_per_conv = _PLATFORM_PROFILE.get(platform, _DEFAULT_PROFILE)
        daily_budget = budget_vnd / max((run_end - run_start).days + 1, 1)

        rng = stable_rng(f"perf:{campaign_code}")
        current = run_start
        while current <= run_end:
            impressions = rng.randint(*imp_range)
            clicks = int(impressions * ctr * rng.uniform(0.8, 1.2))
            conversions = int(clicks * cvr * rng.uniform(0.7, 1.3))
            spend = round(daily_budget * rng.uniform(0.85, 1.05), 2)
            revenue = round(conversions * rev_per_conv * rng.uniform(0.9, 1.1), 2)

            cursor.execute(
                f"""
                INSERT INTO {_table('crm_campaign_performance_daily')}
                    (performance_id, tenant_id, campaign_id, report_date,
                     spend, impressions, clicks, conversions, revenue_estimated)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, campaign_id, report_date) DO UPDATE SET
                    spend = EXCLUDED.spend,
                    impressions = EXCLUDED.impressions,
                    clicks = EXCLUDED.clicks,
                    conversions = EXCLUDED.conversions,
                    revenue_estimated = EXCLUDED.revenue_estimated,
                    updated_at = now();
                """,
                (
                    demo_id(f"perf:{campaign_code}:{current.isoformat()}"),
                    DEMO_TENANT_ID, campaign_id, current,
                    spend, impressions, clicks, conversions, revenue,
                ),
            )
            current += timedelta(days=1)


def reset_tenant_scoped_demo_tables(cursor) -> None:
    logger.info("Resetting previous demo rows in tenant-scoped tables (relations/contacts/transactions/events/content)...")
    cursor.execute(f"DELETE FROM {_table('cdp_relations')} WHERE tenant_id = %s;", (DEMO_TENANT_ID,))
    cursor.execute(f"DELETE FROM {_table('crm_customer_contacts')} WHERE tenant_id = %s;", (DEMO_TENANT_ID,))
    cursor.execute(f"DELETE FROM {_table('crm_transactions')} WHERE tenant_id = %s;", (DEMO_TENANT_ID,))
    cursor.execute(f"DELETE FROM {_table('cdp_raw_events')} WHERE tenant_id = %s;", (DEMO_TENANT_ID,))
    cursor.execute(f"DELETE FROM {_table('cdp_content_items')} WHERE tenant_id = %s;", (DEMO_TENANT_ID,))
    cursor.execute(f"DELETE FROM {_table('crm_campaign_performance_daily')} WHERE tenant_id = %s;", (DEMO_TENANT_ID,))
    cursor.execute(f"DELETE FROM {_table('graph_edges')} WHERE metadata->>'demo_tenant' = %s;", (DEMO_TENANT_ID,))


def seed_relations(cursor, master_profiles: list) -> None:
    if len(master_profiles) < 4:
        logger.warning("Not enough master profiles to seed cdp_relations demo rows -- skipping.")
        return
    logger.info("Seeding cdp_relations between resolved master profiles...")

    def _link(a, b, code):
        cursor.execute(
            f"""
            INSERT INTO {_table('cdp_relations')}
                (tenant_id, source_master_id, target_master_id, relation_type_id)
            SELECT %s, %s, %s, relation_type_id FROM {_table('cdp_relation_types')} WHERE code = %s
            ON CONFLICT (tenant_id, source_master_id, target_master_id, relation_type_id) DO NOTHING;
            """,
            (DEMO_TENANT_ID, a, b, code),
        )

    by_domain = {}
    for m in master_profiles:
        by_domain.setdefault(m["domain"], []).append(m)
    domains = list(by_domain.keys())

    # Link first two profiles within each domain (if enough profiles exist).
    for domain, members in by_domain.items():
        if len(members) >= 2:
            relation_code = "family" if domain == "banking" else "friend"
            _link(members[0]["master_profile_id"], members[1]["master_profile_id"], relation_code)

    # Cross-domain customer-contact links between the first profile of a few domains.
    if len(domains) >= 2 and by_domain[domains[0]] and by_domain[domains[1]]:
        _link(
            by_domain[domains[0]][0]["master_profile_id"],
            by_domain[domains[1]][0]["master_profile_id"],
            "customer-contact",
        )


def seed_customer_contacts(cursor, master_profiles: list) -> None:
    logger.info("Seeding crm_customer_contacts (CS/call-center interaction log)...")
    channels = ("call_center", "live_chat", "email", "branch_visit")
    types = ("inquiry", "complaint", "feedback", "support_request")
    for m in master_profiles:
        rng = stable_rng(f"contacts:{m['master_profile_id']}")
        for _ in range(rng.randint(1, 3)):
            cursor.execute(
                f"""
                INSERT INTO {_table('crm_customer_contacts')}
                    (contact_id, tenant_id, master_profile_id, contact_type, contact_channel,
                     contact_content, contact_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s);
                """,
                (
                    str(uuid.uuid4()), DEMO_TENANT_ID, m["master_profile_id"],
                    rng.choice(types), rng.choice(channels),
                    "Synthetic demo interaction log entry.",
                    datetime.now() - timedelta(days=rng.randint(1, 60), hours=rng.randint(0, 23)),
                ),
            )


RETAIL_TRANSACTIONS = [
    ("POS", "purchase", "product", "Retail Store Purchase", (100_000, 2_000_000), "pos"),
    ("WebStore", "purchase", "product", "Online Order", (150_000, 3_000_000), "web"),
]
BANKING_TRANSACTIONS = [
    ("CoreBanking", "transfer", "account", "Interbank Transfer", (500_000, 20_000_000), "mobile_app"),
    ("CoreBanking", "bill_payment", "account", "Utility Bill Payment", (100_000, 1_500_000), "internet_banking"),
]
REAL_ESTATE_TRANSACTIONS = [
    ("PropertyPortal", "property_inquiry", "property", "Property Inquiry", (1_000_000_000, 5_000_000_000), "web"),
]
TRAVEL_TRANSACTIONS = [
    ("OTA", "booking", "booking", "Travel Booking", (1_000_000, 15_000_000), "mobile_app"),
]
MEDIA_TRANSACTIONS = [
    ("StreamingPlatform", "subscription", "subscription", "Media Subscription", (50_000, 500_000), "mobile_app"),
]
EDUCATION_TRANSACTIONS = [
    ("LearningPlatform", "course_enrollment", "course", "Course Enrollment", (500_000, 5_000_000), "web"),
]


def seed_transactions(cursor, master_profiles: list) -> None:
    logger.info("Seeding crm_transactions per domain...")
    for m in master_profiles:
        rng = stable_rng(f"transactions:{m['master_profile_id']}")
        domain = m["domain"]
        catalog = {
            "retail": RETAIL_TRANSACTIONS,
            "banking": BANKING_TRANSACTIONS,
            "real_estate": REAL_ESTATE_TRANSACTIONS,
            "travel": TRAVEL_TRANSACTIONS,
            "media": MEDIA_TRANSACTIONS,
            "education": EDUCATION_TRANSACTIONS,
        }.get(domain)
        if catalog is None:
            continue
            source_system, txn_type, entity_type, entity_name, amount_range, channel = rng.choice(catalog)
            cursor.execute(
                f"""
                INSERT INTO {_table('crm_transactions')}
                    (transaction_id, tenant_id, master_profile_id, source_system, transaction_type,
                     transaction_status, entity_type, entity_name, amount, currency, channel,
                     transaction_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                """,
                (
                    str(uuid.uuid4()), DEMO_TENANT_ID, m["master_profile_id"], source_system, txn_type,
                    "completed", entity_type, entity_name, rng.randint(*amount_range), "VND", channel,
                    datetime.now() - timedelta(days=rng.randint(1, 90), hours=rng.randint(0, 23)),
                ),
            )

    # A couple of NOT-YET-resolved transactions (master_profile_id = NULL) --
    # demonstrates the same async-backfill pattern as cdp_raw_events, and the
    # ux_crm_transactions_tenant_source dedup-safety unique index.
    rng = stable_rng("unresolved_transactions")
    for i in range(2):
        cursor.execute(
            f"""
            INSERT INTO {_table('crm_transactions')}
                (transaction_id, tenant_id, master_profile_id, source_system, source_transaction_id,
                 transaction_type, transaction_status, entity_type, amount, currency, channel,
                 transaction_time)
            VALUES (%s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, source_system, source_transaction_id) WHERE source_transaction_id IS NOT NULL
            DO NOTHING;
            """,
            (
                str(uuid.uuid4()), DEMO_TENANT_ID, "POS", f"pos-unresolved-{i}",
                "purchase", "completed", "product", rng.randint(50_000, 500_000), "VND", "pos",
                datetime.now() - timedelta(hours=rng.randint(1, 48)),
            ),
        )


RETAIL_EVENTS = [
    ("GENERAL", "page-view", None, None, False),
    ("GENERAL", "search", None, None, False),
    ("COMMERCE", "add-to-cart", (100_000, 500_000), "product", False),
    ("COMMERCE", "purchase", (200_000, 2_000_000), "product", True),
    ("FEEDBACK", "submit-csat-form", None, None, False),
]
BANKING_EVENTS = [
    ("GENERAL", "user-login", None, None, False),
    ("GENERAL", "dashboard-view", None, None, False),
    ("FINANCE", "fund-transfer", (500_000, 20_000_000), "account", True),
    ("FINANCE", "bill-payment", (100_000, 1_500_000), "account", True),
    ("STOCK_TRADING", "trade-executed", (1_000_000, 50_000_000), "security", True),
    ("FEEDBACK", "submit-satisfaction-survey", None, None, False),
]
MEDIA_EVENTS = [
    ("GENERAL", "user-login", None, None, False),
    ("GENERAL", "content-view", None, None, False),
    ("GENERAL", "search", None, None, False),
    ("FEEDBACK", "submit-csat-form", None, None, False),
]
EDUCATION_EVENTS = [
    ("GENERAL", "user-login", None, None, False),
    ("EDUCATION", "course-started", None, "course", False),
    ("EDUCATION", "assignment-submitted", None, "assignment", False),
    ("FEEDBACK", "submit-csat-form", None, None, False),
]

# Anonymous (no resolved profile yet) events for domains not otherwise
# represented in a given CIR demo dataset.
UNRESOLVED_EVENTS = [
    ("travel", "TRAVEL", "search-flight", None, None, False),
    ("travel", "TRAVEL", "booking", (1_000_000, 8_000_000), "booking", True),
    ("real_estate", "REAL_ESTATE", "view-property", None, "property", False),
    ("real_estate", "REAL_ESTATE", "request-property-tour", None, "property", False),
    ("media", "GENERAL", "content-view", None, "content", False),
    ("education", "EDUCATION", "course-started", None, "course", False),
]


def seed_raw_profiles_for_anonymous_events(cursor) -> dict:
    """Creates raw profiles for anonymous/unresolved events (travel/real_estate domains).
    
    Returns a dict mapping (domain, device_id) to raw_profile_id so events can reference them.
    """
    logger.info("Seeding raw profiles for anonymous events (travel/real_estate)...")
    raw_profile_map = {}
    rng = stable_rng("anonymous_raw_profiles")
    
    # Create raw profiles for travel and real_estate domains
    for domain, _, _, _, _, _ in UNRESOLVED_EVENTS:
        # Create a few raw profiles per domain for variety
        for i in range(3):
            device_id = f"demo-anon-device-{domain}-{i}-{rng.randint(1000, 9999)}"
            raw_profile_id = str(uuid.uuid4())
            
            cursor.execute(
                f"""
                INSERT INTO {_table('cdp_raw_profiles_stage')}
                    (raw_profile_id, tenant_id, domain, source_system, channel, device_id,
                     event_name, status_code)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
                """,
                (
                    raw_profile_id, DEMO_TENANT_ID, domain, "WebTracking", "web",
                    device_id, "page-view", 1,
                ),
            )
            raw_profile_map[(domain, device_id)] = raw_profile_id
    
    return raw_profile_map


def seed_raw_events(cursor, master_profiles: list, raw_profile_map: dict = None) -> None:
    logger.info(
        "Seeding cdp_raw_events for all master profiles (minimum %d events/profile)...",
        MIN_EVENTS_PER_MASTER_PROFILE,
    )
    for m in master_profiles:
        rng = stable_rng(f"events:{m['master_profile_id']}")
        catalog = RETAIL_EVENTS if m["domain"] == "retail" else BANKING_EVENTS
        for category, event_name, value_range, entity_type, is_conversion in catalog:
            cursor.execute(
                f"""
                INSERT INTO {_table('cdp_raw_events')}
                    (tenant_id, domain, master_profile_id, raw_profile_id, source_system, channel, event_category,
                     event_name, is_conversion, entity_type, event_value, currency, event_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                """,
                (
                    DEMO_TENANT_ID, m["domain"], m["master_profile_id"], m["first_seen_raw_profile_id"],
                    "AppsFlyer" if m["domain"] == "retail" else "CoreBanking",
                    "mobile_app", category, event_name, is_conversion, entity_type,
                    rng.randint(*value_range) if value_range else None, "VND",
                    datetime.now() - timedelta(days=rng.randint(1, 60), hours=rng.randint(0, 23)),
                ),
            )

        # Add deterministic extra events so every master profile has >10 rows.
        extra_events_needed = max(0, MIN_EVENTS_PER_MASTER_PROFILE - len(catalog))
        for _ in range(extra_events_needed):
            category, event_name, value_range, entity_type, is_conversion = rng.choice(catalog)
            cursor.execute(
                f"""
                INSERT INTO {_table('cdp_raw_events')}
                    (tenant_id, domain, master_profile_id, raw_profile_id, source_system, channel, event_category,
                     event_name, is_conversion, entity_type, event_value, currency, event_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                """,
                (
                    DEMO_TENANT_ID, m["domain"], m["master_profile_id"], m["first_seen_raw_profile_id"],
                    "AppsFlyer" if m["domain"] == "retail" else "CoreBanking",
                    "mobile_app", category, event_name, is_conversion, entity_type,
                    rng.randint(*value_range) if value_range else None, "VND",
                    datetime.now() - timedelta(days=rng.randint(1, 60), hours=rng.randint(0, 23)),
                ),
            )

    logger.info("Seeding anonymous cdp_raw_events for travel/real_estate domains (no resolved profile yet)...")
    if raw_profile_map is None:
        raw_profile_map = {}
    
    rng = stable_rng("unresolved_events")
    for domain, category, event_name, value_range, entity_type, is_conversion in UNRESOLVED_EVENTS:
        # Use device_ids from our raw_profile_map to ensure FK constraint is satisfied
        raw_profile_keys = [(d, dev_id) for (d, dev_id) in raw_profile_map.keys() if d == domain]
        if raw_profile_keys:
            domain_to_use, device_id = rng.choice(raw_profile_keys)
            raw_profile_id = raw_profile_map[(domain_to_use, device_id)]
        else:
            # Fallback: should not happen if raw_profile_map was properly populated
            continue
        
        cursor.execute(
            f"""
            INSERT INTO {_table('cdp_raw_events')}
                (tenant_id, domain, device_id, raw_profile_id, source_system, channel, event_category, event_name,
                 is_conversion, entity_type, event_value, currency, event_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (
                DEMO_TENANT_ID, domain, device_id, raw_profile_id, "WebTracking", "web",
                category, event_name, is_conversion, entity_type,
                rng.randint(*value_range) if value_range else None, "VND",
                datetime.now() - timedelta(days=rng.randint(1, 30)),
            ),
        )


def validate_min_events_per_master_profile(cursor, min_events: int = MIN_EVENTS_PER_MASTER_PROFILE) -> None:
    """Raises if any resolved demo master profile has fewer than ``min_events`` rows in cdp_raw_events."""
    cursor.execute(
        f"""
        SELECT mp.master_profile_id, COUNT(e.event_id) AS event_count
        FROM {_table('cdp_master_profiles')} mp
        LEFT JOIN {_table('cdp_raw_events')} e
          ON e.tenant_id = mp.tenant_id AND e.master_profile_id = mp.master_profile_id
        WHERE mp.tenant_id = %s
        GROUP BY mp.master_profile_id
        HAVING COUNT(e.event_id) < %s
        ORDER BY event_count ASC, mp.master_profile_id
        LIMIT 10;
        """,
        (DEMO_TENANT_ID, min_events),
    )
    violations = cursor.fetchall()
    if violations:
        sample = ", ".join(f"{row['master_profile_id']}({row['event_count']})" for row in violations)
        raise RuntimeError(
            f"Demo invariant failed: each master profile must have >= {min_events} events in "
            f"{_table('cdp_raw_events')}. Sample violations: {sample}"
        )


CONTENT_ITEM_TYPES = ("news", "video", "product", "article")
CONTENT_ITEMS_PER_TYPE_PER_PROFILE = 6

CONTENT_TYPE_DEFAULTS = {
    "news": {
        "cta_label": "Read now",
        "url_path": "insights",
        "summary": "Concise market and customer intelligence tailored to this audience.",
    },
    "video": {
        "cta_label": "Watch now",
        "url_path": "videos",
        "summary": "Short-form educational and promotional video content.",
    },
    "product": {
        "cta_label": "View offer",
        "url_path": "offers",
        "summary": "Product or service recommendations aligned to current behavior.",
    },
    "article": {
        "cta_label": "Explore",
        "url_path": "articles",
        "summary": "Long-form explainers and best-practice guides for this segment.",
    },
}


def seed_content_items(cursor, master_profiles: list) -> None:
    logger.info(
        "Seeding cdp_content_items (%d items/type/profile across %d profile(s))...",
        CONTENT_ITEMS_PER_TYPE_PER_PROFILE,
        len(master_profiles),
    )
    for m in master_profiles:
        master_id = m["master_profile_id"]
        domain = m["domain"]
        rng = stable_rng(f"content:{master_id}")
        base_tags = list(m.get("segmentation_tags") or [])
        if not base_tags:
            base_tags = [domain, "all_profiles"]
        profile_tag = f"profile_{str(master_id).replace('-', '')[:12]}"
        tags = list(dict.fromkeys(base_tags + [profile_tag]))

        for item_type in CONTENT_ITEM_TYPES:
            defaults = CONTENT_TYPE_DEFAULTS[item_type]
            for idx in range(CONTENT_ITEMS_PER_TYPE_PER_PROFILE):
                position = idx + 1
                title = f"{domain.replace('_', ' ').title()} {item_type.title()} {position} for {str(master_id)[:8]}"
                cursor.execute(
                    f"""
                    INSERT INTO {_table('cdp_content_items')}
                        (tenant_id, domain, item_type, title, summary, image_url, cta_label, cta_url,
                         segment_tags, published_at, status_code)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1);
                    """,
                    (
                        DEMO_TENANT_ID,
                        domain,
                        item_type,
                        title,
                        defaults["summary"],
                        f"https://picsum.photos/seed/{str(master_id)[:8]}-{item_type}-{position}/640/360",
                        defaults["cta_label"],
                        f"https://demo.customer360.local/{defaults['url_path']}/{str(master_id)[:8]}-{item_type}-{position}",
                        tags,
                        datetime.now() - timedelta(days=rng.randint(0, 45), hours=rng.randint(0, 23)),
                    ),
                )


def seed_graph_edges(cursor, crm_ids: dict, master_profiles: list) -> None:
    logger.info("Seeding graph_edges (belongs_to/converted/has/belongs_to_industry/is_connected_to/is_from)...")
    metadata = Json({"demo_tenant": DEMO_TENANT_ID})
    edges = []
    if crm_ids["contact"] and crm_ids["account"]:
        account_id = list(crm_ids["account"].values())[0]
        edges.append(("belongs_to", crm_ids["contact"][0], "crm_contact", account_id, "crm_account"))
        edges.append(("belongs_to_industry", account_id, "crm_account", list(crm_ids["industry"].values())[0], "crm_industry"))
    if crm_ids["lead"] and crm_ids["contact"]:
        edges.append(("converted", crm_ids["lead"][0], "crm_lead", crm_ids["contact"][0], "crm_contact"))
    if crm_ids["lead"] and crm_ids["lead_source"]:
        edges.append(("is_from", crm_ids["lead"][0], "crm_lead", list(crm_ids["lead_source"].values())[0], "crm_lead_source"))
    if crm_ids["account"] and crm_ids["opportunity"]:
        edges.append(("has", list(crm_ids["account"].values())[0], "crm_account", crm_ids["opportunity"][0], "crm_opportunity"))
    if len(master_profiles) >= 2:
        edges.append((
            "is_connected_to", master_profiles[0]["master_profile_id"], "cdp_master_profiles",
            master_profiles[1]["master_profile_id"], "cdp_master_profiles",
        ))

    for relation, from_id, from_type, to_id, to_type in edges:
        cursor.execute(
            f"""
            INSERT INTO {_table('graph_edges')}
                (from_id, to_id, from_type, to_type, relation, description, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s);
            """,
            (from_id, to_id, from_type, to_type, relation, f"Demo edge: {from_type} -{relation}-> {to_type}.", metadata),
        )


# crm_contact <-> cdp_master_profiles are two SEPARATE domains in
# database-schema.sql -- crm_contact has no tenant_id/master_profile_id
# column at all, and cdp_master_profiles has nothing pointing back to
# crm_contact. There is NO natural FK/shared key between them (see the
# discussion in the session this was added). The only schema-supported way to
# express "this resolved consumer profile is ALSO this B2B contact/decision-
# maker" is the generic graph_edges table -- which is exactly the join key
# this function seeds, in both directions (a graph_edges row, plus a
# denormalized cross-reference id on each side for quick lookups without a
# graph_edges join).
CONTACT_MASTER_LINK_ACCOUNTS = (
    # (account_name, contact_defs index, domain, master_profiles index within that domain)
    ("Sacombank Digital", 0, "banking", 0),
    ("Techcombank Wealth Partners", 1, "banking", 1),
    ("VinMart Retail Group", 2, "retail", 0),
    ("Saigon Co.op Omnichannel", 3, "retail", 1),
)


def link_crm_contacts_to_master_profiles(cursor, crm_ids: dict, master_profiles: list) -> None:
    """Bridges a handful of ``crm_contact`` rows to real resolved
    ``cdp_master_profiles`` rows -- demonstrating that a B2B decision-maker
    (CRM contact) can ALSO be a resolved individual consumer identity (CIR
    golden record), even though the two tables share no FK.

    Writes the link in three redundant, mutually-consistent ways so it's
    discoverable/joinable regardless of which table you start from:
      1. A ``graph_edges`` row (``relation = 'is_active_as'``,
         ``cdp_master_profiles -> crm_contact``) -- the canonical, generic
         cross-entity relationship record.
      2. ``cdp_master_profiles.attributes->>'linked_crm_contact_id'`` --
         quick lookup from the master-profile side without a graph_edges join.
      3. ``crm_contact.metadata->>'linked_master_profile_id'`` -- quick
         lookup from the CRM-contact side without a graph_edges join.
    """
    banking = [m["master_profile_id"] for m in master_profiles if m["domain"] == "banking"]
    retail = [m["master_profile_id"] for m in master_profiles if m["domain"] == "retail"]
    domain_pools = {"banking": banking, "retail": retail}
    contacts = crm_ids["contact"]

    metadata = Json({"demo_tenant": DEMO_TENANT_ID})
    links = []
    for account_name, contact_index, domain, pool_index in CONTACT_MASTER_LINK_ACCOUNTS:
        pool = domain_pools.get(domain)
        if pool is None or contact_index >= len(contacts) or pool_index >= len(pool):
            continue
        links.append((pool[pool_index], contacts[contact_index], account_name))

    if not links:
        logger.warning("Not enough resolved master profiles/CRM contacts to link -- skipping.")
        return

    logger.info("Linking %d crm_contact row(s) to real resolved cdp_master_profiles rows...", len(links))
    for master_id, contact_id, account_name in links:
        cursor.execute(
            f"""
            INSERT INTO {_table('graph_edges')}
                (from_id, to_id, from_type, to_type, relation, description, metadata)
            VALUES (%s, %s, 'cdp_master_profiles', 'crm_contact', 'is_active_as', %s, %s);
            """,
            (
                master_id, contact_id,
                f"This resolved consumer profile is also the B2B contact/decision-maker at {account_name}.",
                metadata,
            ),
        )
        cursor.execute(
            f"""
            UPDATE {_table('cdp_master_profiles')}
            SET attributes = COALESCE(attributes, '{{}}'::jsonb) || %s
            WHERE master_profile_id = %s;
            """,
            (Json({"linked_crm_contact_id": contact_id}), master_id),
        )
        cursor.execute(
            f"""
            UPDATE {_table('crm_contact')}
            SET metadata = COALESCE(metadata, '{{}}'::jsonb) || %s
            WHERE contact_id = %s;
            """,
            (Json({"linked_master_profile_id": master_id}), contact_id),
        )


# --------------------------------------------------------------------------
# 5. cdp_master_profiles enrichment
# --------------------------------------------------------------------------

RETAIL_CHANNELS = ("Mobile App", "Website")
BANKING_CHANNELS = ("Internet Banking App", "Mobile App", "Branch")
REAL_ESTATE_CHANNELS = ("Property Portal", "Mobile App", "Office Visit")
TRAVEL_CHANNELS = ("Airline App", "OTA Website", "Mobile App")
MEDIA_CHANNELS = ("Streaming App", "Website", "Mobile App")
EDUCATION_CHANNELS = ("Learning Platform", "Mobile App", "Website")
LIFECYCLE_STAGES = ("prospect", "lead", "customer", "vip", "dormant", "churn_risk")
OCCUPATIONS = ("engineer", "teacher", "business_owner", "student", "civil_servant", "sales_professional")
INCOME_SEGMENTS = ("low", "medium", "high")
CITIES = ("Ho Chi Minh City", "Hanoi", "Da Nang", "Can Tho")


def _make_persona_summary(domain: str, lifecycle_stage: str, preferred_channel: str, rng: random.Random) -> str:
    flavor = rng.choice(
        [
            "discovered the brand through a paid social campaign",
            "was referred by an existing customer",
            "signed up directly via organic search",
            "engaged first through an offline event",
        ]
    )
    return (
        f"{domain.capitalize()} profile who {flavor}; primarily engages via {preferred_channel}; "
        f"currently in the '{lifecycle_stage}' lifecycle stage."
    )


def enrich_master_profiles(cursor, master_profiles: list) -> None:
    logger.info("Enriching %d master profiles with lifecycle/ML-scoring/domain-specific fields...", len(master_profiles))
    for m in master_profiles:
        master_id = m["master_profile_id"]
        domain = m["domain"]
        rng = stable_rng(f"enrich:{master_id}")

        lifecycle_stage = rng.choice(LIFECYCLE_STAGES)
        is_established_customer = lifecycle_stage in ("customer", "vip", "dormant", "churn_risk")
        preferred_channel = rng.choice(
            RETAIL_CHANNELS if domain == "retail" else
            BANKING_CHANNELS if domain == "banking" else
            REAL_ESTATE_CHANNELS if domain == "real_estate" else
            TRAVEL_CHANNELS if domain == "travel" else
            MEDIA_CHANNELS if domain == "media" else
            EDUCATION_CHANNELS
        )
        customer_since = (
            (m["created_at"] - timedelta(days=rng.randint(0, 400))).date() if is_established_customer else None
        )
        last_activity_at = datetime.now() - timedelta(days=rng.randint(0, 30), hours=rng.randint(0, 23))

        num_sources = len(m.get("source_systems") or [])
        identity_confidence_score = min(1.0, round(0.5 + 0.15 * num_sources, 4))
        # More realistic churn distribution: ~65% low, ~20% medium, ~10% high, ~5% critical
        churn_rand = rng.random()
        if churn_rand < 0.65:
            churn_probability = round(rng.uniform(0.0, 0.25), 4)  # Low risk: 0-25%
        elif churn_rand < 0.85:
            churn_probability = round(rng.uniform(0.25, 0.55), 4)  # Medium risk: 25-55%
        elif churn_rand < 0.95:
            churn_probability = round(rng.uniform(0.55, 0.80), 4)  # High risk: 55-80%
        else:
            churn_probability = round(rng.uniform(0.80, 1.0), 4)   # Critical risk: 80-100%
        churn_risk_tier = (
            "critical" if churn_probability >= 0.85 else
            "high" if churn_probability >= 0.6 else
            "medium" if churn_probability >= 0.3 else "low"
        )
        lead_conversion_probability = round(rng.uniform(0, 1), 4)
        lead_grade = "Hot" if lead_conversion_probability >= 0.7 else "Warm" if lead_conversion_probability >= 0.4 else "Cold"
        historical_clv = round(rng.uniform(500, 5000) * (10 if domain == "banking" else 1), 2)
        predictive_clv = round(historical_clv * rng.uniform(1.0, 1.8), 2)
        clv_high_threshold = 30000 if domain == "banking" else 3000
        clv_medium_threshold = 10000 if domain == "banking" else 1000
        clv_segment = "high" if predictive_clv > clv_high_threshold else "medium" if predictive_clv > clv_medium_threshold else "low"
        engagement_score = round(rng.uniform(0, 100), 2)
        latest_nps_score = rng.randint(0, 10)
        average_csat = round(rng.uniform(1, 5), 2)
        overall_sentiment_score = round(rng.uniform(-1, 1), 4)
        profile_completeness_score = round(rng.uniform(40, 100), 2)
        segmentation_tags = [domain, lifecycle_stage, clv_segment + "_value"]
        communication_preferences = Json(
            {
                "email_opt_in": rng.random() < 0.7,
                "sms_opt_in": rng.random() < 0.45,
                "push_opt_in": rng.random() < 0.8,
            }
        )
        attributes = Json({"occupation": rng.choice(OCCUPATIONS), "income_segment": rng.choice(INCOME_SEGMENTS)})
        model_versions = Json({
            "churn_model": "v1", "clv_model": "v1", "lead_scoring_model": "v1",
            "cx_scoring_model": "v1", "data_quality_model": "v1",
            "identity_resolution_scoring_model": "v1",
        })
        gender = rng.choice(("male", "female", "other"))
        address = Json({"city": rng.choice(CITIES), "country": "VN"})
        profile_picture_url = f"https://api.dicebear.com/7.x/identicon/svg?seed={master_id}"
        persona_summary = _make_persona_summary(domain, lifecycle_stage, preferred_channel, rng)

        set_clauses = [
            "lifecycle_stage = %s", "preferred_channel = %s", "customer_since = %s",
            "last_activity_at = %s", "churn_probability = %s", "churn_risk_tier = %s",
            "lead_conversion_probability = %s", "lead_grade = %s", "historical_clv = %s",
            "predictive_clv = %s", "clv_segment = %s", "engagement_score = %s",
            "latest_nps_score = %s", "average_csat = %s", "overall_sentiment_score = %s",
            "profile_completeness_score = %s", "identity_confidence_score = %s",
            "segmentation_tags = %s", "communication_preferences = COALESCE(communication_preferences, '{}'::jsonb) || %s",
            "attributes = COALESCE(attributes, '{}'::jsonb) || %s",
            "model_versions = %s", "scores_updated_at = NOW()", "gender = %s", "address = %s",
            "profile_picture_url = %s", "persona_summary = %s",
            # acquisition_source/acquisition_campaign: genuinely derivable from the
            # raw profile that first created this master, via first_seen_raw_profile_id.
            f"""acquisition_source = COALESCE(acquisition_source, (
                SELECT media_source FROM {_table('cdp_raw_profiles_stage')}
                WHERE raw_profile_id = {_table('cdp_master_profiles')}.first_seen_raw_profile_id
            ))""",
            f"""acquisition_campaign = COALESCE(acquisition_campaign, (
                SELECT campaign FROM {_table('cdp_raw_profiles_stage')}
                WHERE raw_profile_id = {_table('cdp_master_profiles')}.first_seen_raw_profile_id
            ))""",
        ]
        params = [
            lifecycle_stage, preferred_channel, customer_since, last_activity_at,
            churn_probability, churn_risk_tier, lead_conversion_probability, lead_grade,
            historical_clv, predictive_clv, clv_segment, engagement_score, latest_nps_score,
            average_csat, overall_sentiment_score, profile_completeness_score,
            identity_confidence_score, segmentation_tags, communication_preferences, attributes, model_versions,
            gender, address, profile_picture_url, persona_summary,
        ]

        if domain == "retail":
            set_clauses += ["loyalty_id = %s", "membership_tier = %s", "preferred_store_code = %s"]
            params += [
                f"LOY-{master_id[:8]}",
                rng.choice(("Silver", "Gold", "Platinum")),
                f"STORE-{rng.randint(1, 20):03d}",
            ]
        elif domain == "banking":
            set_clauses += ["cif_number = %s", "account_numbers = %s", "kyc_status = %s", "risk_segment = %s"]
            # Realistic KYC distribution: ~70% verified, ~15% pending, ~10% unverified, ~5% rejected
            kyc_rand = rng.random()
            if kyc_rand < 0.70:
                kyc_status = "verified"
            elif kyc_rand < 0.85:
                kyc_status = "pending"
            elif kyc_rand < 0.95:
                kyc_status = "unverified"
            else:
                kyc_status = "rejected"
            # Risk segment reflects compliance/operational risk: ~60% low, ~30% medium, ~10% high
            risk_segment_rand = rng.random()
            if risk_segment_rand < 0.60:
                risk_segment = "low"
            elif risk_segment_rand < 0.90:
                risk_segment = "medium"
            else:
                risk_segment = "high"
            params += [
                f"CIF{rng.randint(10_000_000, 99_999_999)}",
                [f"{rng.randint(1000000000, 9999999999)}" for _ in range(rng.randint(1, 2))],
                kyc_status,
                risk_segment,
            ]
        elif domain == "real_estate":
            set_clauses += ["property_types_of_interest = %s", "preferred_location_codes = %s"]
            params += [
                rng.sample(["apartment", "villa", "land", "townhouse", "condo"], k=rng.randint(1, 3)),
                [f"DIST-{rng.randint(1, 12):02d}" for _ in range(rng.randint(1, 2))],
            ]
        elif domain == "travel":
            set_clauses += ["travel_loyalty_program_id = %s", "preferred_travel_class = %s"]
            params += [
                f"TVL-{rng.randint(100000, 999999)}",
                rng.choice(("economy", "business", "first")),
            ]
        elif domain == "media":
            set_clauses += ["media_subscription_id = %s", "preferred_content_genres = %s"]
            params += [
                f"SUB-{rng.randint(100000, 999999)}",
                rng.sample(["news", "sports", "entertainment", "documentary", "music"], k=rng.randint(1, 3)),
            ]
        elif domain == "education":
            set_clauses += ["student_id = %s", "institution_name = %s"]
            params += [
                f"STU-{rng.randint(100000, 999999)}",
                rng.choice(("Demo University", "Demo Online Academy", "Demo Polytechnic")),
            ]
        else:
            # Catch-all for any future domain; do nothing domain-specific.
            pass

        # NOTE: persona_embedding lives on cdp_customer_personas (not
        # cdp_master_profiles) -- see seed_customer_personas() below, which
        # sets it for a representative subset of computed personas.

        params.append(master_id)
        cursor.execute(
            f"UPDATE {_table('cdp_master_profiles')} SET {', '.join(set_clauses)} WHERE master_profile_id = %s;",
            tuple(params),
        )


def seed_customer_personas(cursor, master_profiles: list) -> int:
    """Computes and persists a real customer persona (cdp_customer_personas +
    cdp_persona_features + cdp_persona_score_details + cdp_persona_history)
    for every enriched master profile, via the SAME PersonaResolutionEngine
    backend-system/identity_resolution's CIR pipeline uses in production
    (resolver.py) -- proves the "AI-native Customer Persona Resolution
    Engine" actually works end-to-end against real seeded data, instead of
    duplicating its SQL here. Must run AFTER enrich_master_profiles() (needs
    lifecycle_stage/membership_tier/CLV/etc. already populated) and after
    master_profiles has been refetched to include tenant_id.

    Idempotent / safe to re-run: resolve_persona() always inserts a fresh
    version (deactivating the previous one), so re-running this just adds
    another computed_version rather than erroring.
    """
    logger.info("Computing customer personas for %d master profiles via PersonaResolutionEngine...", len(master_profiles))
    engine = PersonaResolutionEngine(schema=DB_SCHEMA)
    computed = 0
    embedded = 0
    for m in master_profiles:
        result = engine.resolve_persona(cursor, DEMO_TENANT_ID, m["master_profile_id"])
        if result is None:
            continue
        computed += 1

        # persona_embedding lives on cdp_customer_personas (identity
        # *understanding*), not cdp_master_profiles -- only seeded for a
        # representative subset of profiles, same convention as the master
        # profile enrichment step used before this table existed.
        if embedded < EMBEDDING_PROFILE_LIMIT:
            embedding_rng = stable_rng(f"persona_embedding:{result['persona_id']}")
            vector_literal = (
                "[" + ",".join(f"{embedding_rng.uniform(-1, 1):.6f}" for _ in range(PERSONA_EMBEDDING_DIM)) + "]"
            )
            cursor.execute(
                f"UPDATE {_table('cdp_customer_personas')} SET persona_embedding = %s::vector({PERSONA_EMBEDDING_DIM}) "
                "WHERE persona_id = %s;",
                (vector_literal, result["persona_id"]),
            )
            embedded += 1

    logger.info("Computed %d personas (%d with a persona_embedding).", computed, embedded)
    return computed



# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def fetch_master_profiles(cursor) -> list:
    cursor.execute(
        f"""
        SELECT master_profile_id, domain, segmentation_tags, source_systems, first_seen_raw_profile_id, created_at
        FROM {_table('cdp_master_profiles')}
        WHERE tenant_id = %s
        ORDER BY created_at;
        """,
        (DEMO_TENANT_ID,),
    )
    return cursor.fetchall()


def main() -> None:
    conn = psycopg2.connect(host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, port=DB_PORT)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            master_profiles = fetch_master_profiles(cursor)
            if not master_profiles:
                logger.error(
                    "No resolved master profiles found for tenant_id=%s -- run "
                    "scripts/init_sample_data.py + scripts/run_demo_resolution.py first.",
                    DEMO_TENANT_ID,
                )
                return

            detail_profiles = master_profiles[:DETAIL_PROFILE_LIMIT]

            seed_relation_types(cursor)
            crm_ids = seed_crm_entities(cursor)
            reset_tenant_scoped_demo_tables(cursor)
            seed_campaign_performance_daily(cursor, crm_ids["campaign"])
            seed_relations(cursor, detail_profiles)
            seed_customer_contacts(cursor, detail_profiles)
            seed_transactions(cursor, detail_profiles)
            raw_profile_map = seed_raw_profiles_for_anonymous_events(cursor)
            seed_raw_events(cursor, master_profiles, raw_profile_map)
            validate_min_events_per_master_profile(cursor)
            seed_graph_edges(cursor, crm_ids, detail_profiles)
            enrich_master_profiles(cursor, master_profiles)
            master_profiles = fetch_master_profiles(cursor)
            personas_computed = seed_customer_personas(cursor, master_profiles)
            seed_content_items(cursor, master_profiles)
            link_crm_contacts_to_master_profiles(cursor, crm_ids, master_profiles)

        conn.commit()
        logger.info(
            "Full demo data seeded: %d master profiles enriched (%d with a persona_embedding), "
            "%d got detail rows (relations/contacts/transactions); all master profiles got >= %d events; "
            "content items: %d/profile/type; %d customer personas computed via PersonaResolutionEngine; "
            "CRM journey graph + "
            "graph_edges + cdp_relation_types seeded; crm_contact <-> cdp_master_profiles linked "
            "via graph_edges ('is_active_as') + cross-referenced attributes/metadata.",
            len(master_profiles), min(len(master_profiles), EMBEDDING_PROFILE_LIMIT), len(detail_profiles),
            MIN_EVENTS_PER_MASTER_PROFILE, CONTENT_ITEMS_PER_TYPE_PER_PROFILE, personas_computed,
        )
    except Exception:
        conn.rollback()
        logger.exception("Failed to seed full demo data.")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
