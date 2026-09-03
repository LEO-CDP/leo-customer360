"""Seeds comprehensive demo data covering every table/column in
core-customer360/database-schema.sql that ``init_sample_data.py`` +
``run_demo_resolution.py`` do NOT already exercise.

Those two scripts only cover the Customer Identity Resolution (CIR) slice:
Adjust raw-profile ingestion -> resolved ``cdp_master_profiles`` rows. This
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
    ``crm_transactions`` (retail purchases, education enrollments/tuition payments --
   including a couple of NOT-YET-identity-resolved rows with
   ``master_profile_id = NULL``, the same async-backfill pattern used by
   ``cdp_raw_events``).
4. ``cdp_raw_events``: sample behavioral events spanning every
   ``event_category`` seeded in ``cdp_event_catalog`` (GENERAL/FEEDBACK/
    COMMERCE/EDUCATION/TRAVEL/REAL_ESTATE), including a few
   travel/real_estate events with NO master profile yet (domains not
   otherwise represented among the Adjust-only CIR demo profiles).
5. ``graph_edges``: a handful of edges spanning several relation partitions
   (``belongs_to``, ``converted``, ``has``, ``belongs_to_industry``,
   ``is_connected_to``, ``is_from``).
6. ``cdp_master_profiles`` enrichment: fills in every column NOT already set
   by ``CustomerIdentityResolver`` -- lifecycle/engagement tracking
   (customer_since/last_activity_at/preferred_channel/lifecycle_stage/
    persona_summary), the full ML scoring block (lead/churn/CLV/CX/data
    quality), retail-only attrs (loyalty_id/membership_tier/
    preferred_store_code) for retail-domain profiles, education-only attrs
    (student_id/institution_name/course_completion_rate/learning_mode) for education-domain
   profiles, acquisition_source/acquisition_campaign (joined back from the
   raw profile that first created the master, via first_seen_raw_profile_id),
   segmentation_tags/attributes/gender/address/profile_picture_url.
7a. ``cdp_persona_archetypes``: two curated "Ideal Customer Profile" (ICP)
   archetypes per ``sys_domain`` (a premium/champion target and an emerging/
   growth target), each tied to a concrete product and campaign time window,
   with a declared centroid component-score vector + ``persona_embedding``.
   Every master profile then gets its own six component scores computed
   (via ``persona_engine.compute_persona()``) and is lookalike-matched
   (cosine similarity against the centroids) to the best-fit archetype in
   its domain, persisted as a versioned ``cdp_customer_personas`` row --
   see ``seed_persona_archetypes()`` / ``seed_customer_personas()``.
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
tables (see init_sample_data.py's hash_pii()): ``secondary_emails``/
``secondary_phones`` and ``date_of_birth``. ``address`` is populated with
city/country only (no street). ``gender`` and ``profile_picture_url`` ARE
populated -- neither is independently identifying PII.

Global demo exception: master-profile name fields
``full_name``/``first_name``/``last_name`` are rewritten as synthetic
plaintext values for readability in cross-region demos (VN/EU/US naming
mix). Retail profiles also carry plaintext ``email``/``phone_number`` (and
``is_hashed`` is set to ``FALSE``). Other domains may still keep hashed
values in additional PII columns inherited from init_sample_data.py /
run_demo_resolution.py.

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
import math
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

from identity_resolution.persona_engine import PersonaResolutionEngine, compute_persona  # noqa: E402
from identity_resolution.rls import set_tenant_context  # noqa: E402

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
PERSONA_EMBEDDING_DIM = 768
# Demo invariant: every resolved master profile must have >10 behavioral events.
MIN_EVENTS_PER_MASTER_PROFILE = 11

def canonical_demo_domain(domain: str | None) -> str:
    if not domain:
        return "retail"
    return domain


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


def realistic_event_days_ago(rng: random.Random, max_days: int = 365) -> int:
    """Evenly-spread day offset across ``max_days`` (defaults to 365 days / 1 year) so
    seeded cdp_raw_events give the analytics dashboard's Event Activity Heatmap
    real daily-tracking coverage across the full year, instead of clustering
    almost entirely in the most recent week."""
    quarter = max(1, max_days // 4)
    bucket = rng.random()
    if bucket < 0.30:
        return rng.randint(1, min(quarter, max_days))
    if bucket < 0.55:
        return rng.randint(min(quarter + 1, max_days), min(quarter * 2, max_days))
    if bucket < 0.78:
        return rng.randint(min(quarter * 2 + 1, max_days), min(quarter * 3, max_days))
    return rng.randint(min(quarter * 3 + 1, max_days), max_days)


# --------------------------------------------------------------------------
# 1. CRM Journey Graph
# --------------------------------------------------------------------------

INDUSTRIES = [
    ("Education & EdTech", "Online learning platforms, universities, and career upskilling providers."),
    ("Retail & E-commerce", "Omni-channel retail, marketplaces and D2C brands."),
    ("Real Estate", "Residential and commercial property developers/agencies."),
    ("Travel & Hospitality", "Airlines, OTAs, hotel groups and tour operators."),
]

ACCOUNTS = [
    ("NexaLearn", "Education & EdTech"),
    ("BrightForge", "Education & EdTech"),
    ("UrbanNest", "Retail & E-commerce"),
    ("MarketSpring", "Retail & E-commerce"),
    ("TerraPeak", "Real Estate"),
    ("Voyara", "Travel & Hospitality"),

    ("Skillora", "Education & EdTech"),
    ("Learnova", "Education & EdTech"),
    ("Cartiva", "Retail & E-commerce"),
    ("Shopora", "Retail & E-commerce"),
    ("Propella", "Real Estate"),
    ("Tripvera", "Travel & Hospitality"),

    ("Eduvia", "Education & EdTech"),
    ("Mindora", "Education & EdTech"),
    ("Mercanta", "Retail & E-commerce"),
    ("Vendora", "Retail & E-commerce"),
    ("Estatera", "Real Estate"),
    ("Roamora", "Travel & Hospitality"),

    ("Knowlytic", "Education & EdTech"),
    ("Skillverse", "Education & EdTech"),
    ("Retailio", "Retail & E-commerce"),
    ("Commerza", "Retail & E-commerce"),
    ("Landora", "Real Estate"),
    ("Journeva", "Travel & Hospitality"),
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
        "Q4 Education App Enrollment - Google UAC",
        "EDU-Q4-GOOG-UAC-001",
        "Active",
        "Paid Search",
        "Google",
        "Enrollments",
        420_000_000,
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
        "Education Course Completion Push - Adjust Retargeting",
        "EDU-RET-ADJ-005",
        "Active",
        "Push Notification",
        "Adjust",
        "Retention",
        180_000_000,
        -20, 40,
        "adjust", "push",
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
        "Education Webinar Funnel - C360 Tracker",
        "EDU-WEBINAR-C360-007",
        "Active",
        "Owned Media",
        "C360Tracker",
        "Engagement",
        140_000_000,
        -25, 60,
        "c360_tracker", "owned",
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

LEAD_FIRST_NAMES = (
    # Vietnam
    "Minh", "Linh", "Huy", "Trang", "Khoa", "My", "Duc", "Anh",
    "Hoa", "Tuan", "Thao", "Nam", "Phuong", "Quang", "Vy", "Long",

    # United States / Canada
    "Emma", "Noah", "Olivia", "Liam", "Sophia", "Mason", "Ava", "Ethan",
    "Amelia", "James", "Chloe", "Benjamin", "Harper", "Lucas", "Mia", "Henry",

    # Europe
    "Luca", "Sofia", "Mateo", "Elena", "Hugo", "Nora", "Marta", "Leo",
    "Ines", "Jonas", "Clara", "Felix", "Anna", "Theo", "Mila", "Arthur",

    # International
    "Alex", "Daniel", "Maria", "David", "Laura", "Samuel", "Julia", "Max",
)

LEAD_LAST_NAMES = (
    # Vietnam
    "Nguyen", "Tran", "Le", "Pham", "Hoang", "Vo", "Bui", "Dang",
    "Do", "Ho", "Ngo", "Duong", "Phan", "Vu", "Huynh", "Truong",

    # United States / Canada
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Miller", "Davis",
    "Wilson", "Anderson", "Taylor", "Thomas", "Moore", "Jackson", "Martin",
    "Thompson", "White",

    # Europe
    "Schmidt", "Rossi", "Novak", "Dubois", "Kovacs", "Muller", "Garcia",
    "Silva", "Moreau", "Ionescu", "Laurent", "Fischer", "Weber", "Costa",
    "Santos", "Bianchi",

    # International
    "Morgan", "Carter", "Parker", "Bennett", "Cooper", "Reed",
)


VN_PROFILE_FIRST_NAMES = (
    "Minh", "Linh", "Huy", "Trang", "Khoa", "My", "Duc", "Anh",
    "Hoa", "Tuan", "Thao", "Nam", "Phuong", "Quang", "Vy", "Long",
    "Nhi", "Thuy", "Dat", "Mai", "Bao", "Son", "Hung", "Lan",
)

VN_PROFILE_LAST_NAMES = (
    "Nguyen", "Tran", "Le", "Pham", "Hoang", "Vo", "Bui", "Dang",
    "Do", "Ho", "Ngo", "Duong", "Phan", "Vu", "Huynh", "Truong",
    "Dang", "Dinh", "Mai", "Ta", "Cao", "Ly",
)


EU_PROFILE_FIRST_NAMES = (
    "Luca", "Sofia", "Mateo", "Elena", "Hugo", "Nora", "Marta", "Leo",
    "Ines", "Jonas", "Clara", "Felix", "Anna", "Theo", "Mila", "Arthur",
    "Louis", "Amelie", "Marco", "Giulia", "Lorenzo", "Chiara",
    "Nicolas", "Emma", "Freya", "Oscar",
)

EU_PROFILE_LAST_NAMES = (
    "Rossi", "Novak", "Schmidt", "Dubois", "Kovacs", "Muller",
    "Garcia", "Silva", "Moreau", "Ionescu", "Laurent", "Fischer",
    "Weber", "Costa", "Santos", "Bianchi", "Martin", "Bernard",
    "Fontana", "Romano", "Lefevre", "Petrov", "Horvat", "Keller",
)


US_PROFILE_FIRST_NAMES = (
    "Emma", "Olivia", "Ava", "Liam", "Noah", "Mason", "Amelia", "James",
    "Ethan", "Chloe", "Sophia", "Jackson", "Mia", "Lucas", "Harper",
    "Benjamin", "Ella", "Alexander", "Evelyn", "Daniel", "Scarlett",
    "Henry", "Grace", "Michael", "Lily", "William", "Emily",
)

US_PROFILE_LAST_NAMES = (
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Miller",
    "Davis", "Wilson", "Anderson", "Taylor", "Thomas", "Moore",
    "Jackson", "Martin", "Thompson", "White", "Harris", "Clark",
    "Lewis", "Walker", "Hall", "Allen", "Young", "King",
)

def build_global_profile_name(rng: random.Random) -> tuple[str, str, str, str]:
    locale = rng.choices(("vn", "eu", "us"), weights=(0.35, 0.35, 0.30), k=1)[0]
    if locale == "vn":
        first_name = rng.choice(VN_PROFILE_FIRST_NAMES)
        last_name = rng.choice(VN_PROFILE_LAST_NAMES)
        full_name = f"{last_name} {first_name}"
    elif locale == "eu":
        first_name = rng.choice(EU_PROFILE_FIRST_NAMES)
        last_name = rng.choice(EU_PROFILE_LAST_NAMES)
        full_name = f"{first_name} {last_name}"
    else:
        first_name = rng.choice(US_PROFILE_FIRST_NAMES)
        last_name = rng.choice(US_PROFILE_LAST_NAMES)
        full_name = f"{first_name} {last_name}"
    return first_name, last_name, full_name, locale


def email_token(value: str) -> str:
    token = "".join(ch.lower() if ch.isalnum() else "." for ch in value)
    while ".." in token:
        token = token.replace("..", ".")
    return token.strip(".")


def tracking_platform_for_campaign(platform: str) -> str:
    return {
        "Adjust": "adjust",
        "Google": "ga4",
        "C360Tracker": "c360_tracker",
    }.get(platform, platform.lower().replace(" ", "_"))


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
    ids: dict = {
        "industry": {}, "account": {}, "lead_source": {}, "lead": [],
        "campaign": {}, "contact": [], "contact_account_names": [], "opportunity": [],
    }

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
                    "tracking_platform": tracking_platform_for_campaign(platform),
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

    # Keep downstream fixture mapping resilient even if ACCOUNTS display names
    # are changed during demo customization.
    industry_accounts: dict[str, list[str]] = {
        "Education & EdTech": [],
        "Retail & E-commerce": [],
        "Real Estate": [],
        "Travel & Hospitality": [],
    }
    for account_name, industry_name in ACCOUNTS:
        if account_name in ids["account"]:
            industry_accounts.setdefault(industry_name, []).append(account_name)

    all_seeded_accounts = list(ids["account"].keys())
    if not all_seeded_accounts:
        raise RuntimeError("No CRM accounts were seeded; verify ACCOUNTS fixture integrity.")

    fallback_account = all_seeded_accounts[0]

    def _pick_account(industry_name: str, index: int = 0) -> str:
        pool = industry_accounts.get(industry_name) or []
        if index < len(pool):
            return pool[index]
        if pool:
            return pool[0]
        return fallback_account

    contact_defs = [
        (_pick_account("Education & EdTech", 0), 0), (_pick_account("Education & EdTech", 1), 1),
        (_pick_account("Retail & E-commerce", 0), 2), (_pick_account("Retail & E-commerce", 1), 3),
        (_pick_account("Real Estate", 0), 4), (_pick_account("Travel & Hospitality", 0), 5),
    ]
    for account_name, lead_index in contact_defs:
        account_id = ids["account"].get(account_name)
        if account_id is None:
            logger.warning("Skipping crm_contact seed for unknown account '%s'.", account_name)
            continue
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
                f"09{rng.randint(10000000, 99999999)}", account_id,
                f"Primary contact at {account_name}, converted from a demo lead.",
                Json({"converted_from_lead_id": ids["lead"][lead_index]}),
            ),
        )
        ids["contact"].append(contact_id)
        ids["contact_account_names"].append(account_name)

    opportunity_defs = [
        (_pick_account("Education & EdTech", 0), "Learner Retention Analytics Rollout", 900_000_000, "negotiation", 45),
        (_pick_account("Retail & E-commerce", 0), "Loyalty Program Expansion", 350_000_000, "proposal", 30),
        (_pick_account("Real Estate", 0), "CRM + Customer 360 Rollout", 800_000_000, "qualification", 90),
        (_pick_account("Travel & Hospitality", 0), "Booking Personalization Engine", 600_000_000, "closed_won", -10),
    ]
    for account_name, opp_name, value, stage, close_offset in opportunity_defs:
        account_id = ids["account"].get(account_name)
        if account_id is None:
            logger.warning("Skipping crm_opportunity seed for unknown account '%s'.", account_name)
            continue
        opportunity_id = demo_id(f"crm_opportunity:{opp_name}")
        cursor.execute(
            f"""
            INSERT INTO {_table('crm_opportunity')}
                (opportunity_id, tenant_id, account_id, name, value, stage, close_date, description)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (opportunity_id) DO UPDATE SET stage = EXCLUDED.stage, value = EXCLUDED.value;
            """,
            (
                opportunity_id, DEMO_TENANT_ID, account_id, opp_name, value, stage,
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
    "Adjust":  ((3_000, 15_000), 0.060, 0.15, 500_000),  # re-targeting: higher CVR
    "C360Tracker": ((10_000, 55_000), 0.035, 0.09, 750_000),
    "YouTube":    ((30_000, 120_000), 0.005, 0.015, 1_500_000),
}
_DEFAULT_PROFILE = ((5_000, 20_000), 0.03, 0.05, 700_000)

DATA_SOURCES = [
    {
        "name": "Adjust Mobile Attribution",
        "slug": "adjust-mobile-attribution",
        "source_type": 5,
        "status": 1,
        "data_source_url": "https://automate.adjust.com/reports-service/report",
        "thumbnail_url": "https://cdn.example.com/connectors/adjust.png",
        "collect_directly": True,
        "first_party_data": True,
        "journey_level": 3,
        "journey_map_id": "journey-mobile-attribution",
        "touchpoint_hub_id": "touchpoint-mobile-ads",
        "security_code": "ADJ-DEMO-SECURE",
        "total_tracked_event": 120000,
        "avg_daily_event": 3200,
        "avg_events_per_profile": 26.75,
        "access_tokens": {"api_token": "adjust_demo_token"},
        "data_source_hosts": ["automate.adjust.com", "app.adjust.com"],
        "javascript_tags": [],
        "qr_code_data": {},
    },
    {
        "name": "Google Analytics 4",
        "slug": "google-analytics-4",
        "source_type": 1,
        "status": 1,
        "data_source_url": "https://analytics.google.com",
        "thumbnail_url": "https://cdn.example.com/connectors/ga4.png",
        "collect_directly": True,
        "first_party_data": True,
        "journey_level": 3,
        "journey_map_id": "journey-web-analytics",
        "touchpoint_hub_id": "touchpoint-web",
        "security_code": "GA4-DEMO-SECURE",
        "total_tracked_event": 98000,
        "avg_daily_event": 2400,
        "avg_events_per_profile": 18.90,
        "access_tokens": {"measurement_id": "G-DEMO360"},
        "data_source_hosts": ["www.googletagmanager.com", "www.google-analytics.com", "analytics.google.com"],
        "javascript_tags": [
            "<script async src='https://www.googletagmanager.com/gtag/js?id=G-DEMO360'></script>",
            "gtag('config', 'G-DEMO360')",
        ],
        "qr_code_data": {
            "target_url": "https://analytics.google.com",
            "tracking_url": "https://analytics.google.com?utm_source=google-analytics-4&utm_medium=qr_code&utm_campaign=c360_datasource",
            "qr_code_url": "https://api.qrserver.com/v1/create-qr-code/?size=250x250&data=https%3A%2F%2Fanalytics.google.com%3Futm_source%3Dgoogle-analytics-4%26utm_medium%3Dqr_code%26utm_campaign%3Dc360_datasource",
            "generated_at": "2026-08-07T00:00:00Z",
        },
    },
    {
        "name": "C360 Tracker",
        "slug": "c360-tracker",
        "source_type": 1,
        "status": 1,
        "data_source_url": "https://tracker.customer360.local/collect",
        "thumbnail_url": "https://cdn.example.com/connectors/c360-tracker.png",
        "collect_directly": True,
        "first_party_data": True,
        "journey_level": 3,
        "journey_map_id": "journey-c360-tracker",
        "touchpoint_hub_id": "touchpoint-c360-tracker",
        "security_code": "C360-DEMO-SECURE",
        "total_tracked_event": 86000,
        "avg_daily_event": 2100,
        "avg_events_per_profile": 16.8,
        "access_tokens": {"write_key": "c360_tracker_demo_key"},
        "data_source_hosts": ["tracker.customer360.local"],
        "javascript_tags": [
            "window.c360Tracker=window.c360Tracker||{track:function(){return true;}};",
            "c360Tracker.track('page_view', {tenant: 'demo'});",
        ],
        "qr_code_data": {},
    },
]

SCORING_MODELS = [
    {
        "scoring_model_name": "churn_prediction_v2",
        "display_name": "XGBoost Churn Predictor",
        "description": "Calculates the probability of a customer churning in the next 30 days based on engagement drop-offs.",
        "model_type": "classification",
        "status": "ACTIVE",
        "schedule_definition": "0 2 * * *",
        "input_features": ["last_activity_at", "total_spend", "support_tickets_count"],
        "hyperparameters": {"max_depth": 6, "learning_rate": 0.1, "objective": "binary:logistic"},
    },
    {
        "scoring_model_name": "clv_regression_v1",
        "display_name": "Customer Lifetime Value (90-Day)",
        "description": "Predicts total revenue a customer will generate over the next 90 days.",
        "model_type": "regression",
        "status": "ACTIVE",
        "schedule_definition": "0 3 * * 0",
        "input_features": ["historical_clv", "average_order_value", "purchase_frequency"],
        "hyperparameters": {"algorithm": "random_forest_regressor", "n_estimators": 100},
    },
    {
        "scoring_model_name": "b2b_lead_scoring_rules",
        "display_name": "B2B Lead Scoring Engine",
        "description": "Rule-based engine assigning points for email opens, website visits, and job titles.",
        "model_type": "rules_engine",
        "status": "ACTIVE",
        "schedule_definition": "*/15 * * * *",
        "input_features": ["email_opens", "website_visits", "job_title"],
        "hyperparameters": {"weights": {"email_opens": 2, "website_visits": 5, "c_level_title": 20}},
    },
    {
        "scoring_model_name": "cx_sentiment_llm_v1",
        "display_name": "Customer Experience & Sentiment Analyzer",
        "description": "Generative LLM pipeline scoring customer sentiment and feedback risk from interaction logs.",
        "model_type": "generative_llm",
        "status": "ACTIVE",
        "schedule_definition": "0 * * * *",
        "input_features": ["feedback_text", "support_notes", "chat_transcripts"],
        "hyperparameters": {"temperature": 0.2, "model_name": "gpt-4o-mini"},
    },
    {
        "scoring_model_name": "data_quality_cir_confidence",
        "display_name": "Identity Resolution Confidence Model",
        "description": "Evaluates profile completeness, identifier uniqueness, and CIR resolution confidence.",
        "model_type": "classification",
        "status": "ACTIVE",
        "schedule_definition": "0 1 * * *",
        "input_features": ["email_normalized", "phone_normalized", "device_count"],
        "hyperparameters": {"threshold": 0.85},
    },
]


def seed_campaign_performance_daily(cursor, campaign_ids: dict) -> None:
    """Inserts daily performance rows for each seeded campaign.

    Metrics are generated with a per-platform profile and a deterministic RNG
    so the script is fully idempotent.  Only days where the campaign was
    already running (start_date <= today) are inserted.
    """
    logger.info("Seeding crm_campaign_performance_daily with Adjust/GA4/C360 Tracker-style metrics...")
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


def seed_data_sources(cursor) -> None:
    """Seeds tenant-scoped rows in sys_data_source used by metadata/data-sources."""
    logger.info("Seeding sys_data_source catalog for demo tenant...")
    for data_source in DATA_SOURCES:
        data_source_id = demo_id(f"sys_data_source:{data_source['slug']}")
        cursor.execute(
            f"""
            INSERT INTO {_table('sys_data_source')}
                (data_source_id, tenant_id, name, slug, source_type, status,
                 data_source_url, thumbnail_url, collect_directly, first_party_data,
                 journey_level, journey_map_id, touchpoint_hub_id, security_code,
                 total_tracked_event, avg_daily_event, avg_events_per_profile,
                 access_tokens, data_source_hosts,
                 javascript_tags, qr_code_data)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, slug) DO UPDATE SET
                name = EXCLUDED.name,
                source_type = EXCLUDED.source_type,
                status = EXCLUDED.status,
                data_source_url = EXCLUDED.data_source_url,
                thumbnail_url = EXCLUDED.thumbnail_url,
                collect_directly = EXCLUDED.collect_directly,
                first_party_data = EXCLUDED.first_party_data,
                journey_level = EXCLUDED.journey_level,
                journey_map_id = EXCLUDED.journey_map_id,
                touchpoint_hub_id = EXCLUDED.touchpoint_hub_id,
                security_code = EXCLUDED.security_code,
                total_tracked_event = EXCLUDED.total_tracked_event,
                avg_daily_event = EXCLUDED.avg_daily_event,
                avg_events_per_profile = EXCLUDED.avg_events_per_profile,
                access_tokens = EXCLUDED.access_tokens,
                data_source_hosts = EXCLUDED.data_source_hosts,
                javascript_tags = EXCLUDED.javascript_tags,
                qr_code_data = EXCLUDED.qr_code_data,
                updated_at = now();
            """,
            (
                data_source_id,
                DEMO_TENANT_ID,
                data_source["name"],
                data_source["slug"],
                data_source["source_type"],
                data_source["status"],
                data_source["data_source_url"],
                data_source["thumbnail_url"],
                data_source["collect_directly"],
                data_source["first_party_data"],
                data_source["journey_level"],
                data_source["journey_map_id"],
                data_source["touchpoint_hub_id"],
                data_source["security_code"],
                data_source["total_tracked_event"],
                data_source["avg_daily_event"],
                data_source["avg_events_per_profile"],
                Json(data_source["access_tokens"]),
                data_source["data_source_hosts"],
                data_source["javascript_tags"],
                Json(data_source["qr_code_data"]),
            ),
        )


def seed_scoring_models(cursor) -> None:
    """Seeds central catalog rows in cdp_scoring_models."""
    logger.info("Seeding cdp_scoring_models catalog...")
    for model in SCORING_MODELS:
        cursor.execute(
            f"""
            INSERT INTO {_table('cdp_scoring_models')}
                (scoring_model_name, display_name, description, model_type, status,
                 schedule_definition, input_features, hyperparameters)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (scoring_model_name) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                description = EXCLUDED.description,
                model_type = EXCLUDED.model_type,
                status = EXCLUDED.status,
                schedule_definition = EXCLUDED.schedule_definition,
                input_features = EXCLUDED.input_features,
                hyperparameters = EXCLUDED.hyperparameters,
                updated_at = now();
            """,
            (
                model["scoring_model_name"],
                model["display_name"],
                model["description"],
                model["model_type"],
                model["status"],
                model["schedule_definition"],
                model["input_features"],
                Json(model["hyperparameters"]),
            ),
        )


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
        normalized_domain = canonical_demo_domain(m["domain"])
        by_domain.setdefault(normalized_domain, []).append(m)
    domains = list(by_domain.keys())

    # Link first two profiles within each domain (if enough profiles exist).
    for domain, members in by_domain.items():
        if len(members) >= 2:
            relation_code = "colleague" if domain == "education" else "friend"
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
                    datetime.now() - timedelta(days=realistic_event_days_ago(rng), hours=rng.randint(0, 23)),
                ),
            )


RETAIL_TRANSACTIONS = [
    ("POS", "purchase", "product", "Retail Store Purchase", (100_000, 2_000_000), "pos"),
    ("WebStore", "purchase", "product", "Online Order", (150_000, 3_000_000), "web"),
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
    ("LearningPlatform", "tuition_payment", "course", "Tuition Payment", (1_000_000, 15_000_000), "web"),
    ("LearningPlatform", "certification_fee", "certificate", "Certification Exam Fee", (300_000, 3_000_000), "mobile_app"),
]

DOMAIN_TRANSACTION_CATALOG = {
    "retail": RETAIL_TRANSACTIONS,
    "education": EDUCATION_TRANSACTIONS,
    "real_estate": REAL_ESTATE_TRANSACTIONS,
    "travel": TRAVEL_TRANSACTIONS,
    "media": MEDIA_TRANSACTIONS,
}


def seed_transactions(cursor, master_profiles: list) -> None:
    logger.info("Seeding crm_transactions per domain...")
    for m in master_profiles:
        rng = stable_rng(f"transactions:{m['master_profile_id']}")
        domain = canonical_demo_domain(m["domain"])
        catalog = DOMAIN_TRANSACTION_CATALOG.get(domain)
        if catalog is None:
            continue
        # Bug fix: this loop previously sat unreachable after `continue`, so
        # no master profile ever got a crm_transactions row from this branch.
        for _ in range(rng.randint(2, 5)):
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
                    datetime.now() - timedelta(days=realistic_event_days_ago(rng), hours=rng.randint(0, 23)),
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
EDUCATION_EVENTS = [
    ("GENERAL", "user-login", None, None, False),
    ("EDUCATION", "course-started", None, "course", False),
    ("EDUCATION", "lesson-completed", None, "lesson", False),
    ("EDUCATION", "assignment-submitted", None, "assignment", False),
    ("EDUCATION", "exam-booked", (300_000, 3_000_000), "certificate", True),
    ("FEEDBACK", "submit-csat-form", None, None, False),
]
MEDIA_EVENTS = [
    ("GENERAL", "user-login", None, None, False),
    ("GENERAL", "content-view", None, None, False),
    ("GENERAL", "search", None, None, False),
    ("FEEDBACK", "submit-csat-form", None, None, False),
]
REAL_ESTATE_EVENTS = [
    ("GENERAL", "view-property", None, "property", False),
    ("GENERAL", "search", None, None, False),
    ("REAL_ESTATE", "request-property-tour", None, "property", False),
    ("FEEDBACK", "submit-csat-form", None, None, False),
]
TRAVEL_EVENTS = [
    ("GENERAL", "search-flight", None, None, False),
    ("TRAVEL", "booking", (1_000_000, 8_000_000), "booking", True),
    ("GENERAL", "itinerary-view", None, "booking", False),
    ("FEEDBACK", "submit-csat-form", None, None, False),
]

DOMAIN_EVENT_CATALOG = {
    "retail": RETAIL_EVENTS,
    "education": EDUCATION_EVENTS,
    "media": MEDIA_EVENTS,
    "real_estate": REAL_ESTATE_EVENTS,
    "travel": TRAVEL_EVENTS,
}

DOMAIN_EVENT_SOURCE_SYSTEM = {
    "retail": "Adjust",
    "education": "GoogleAnalytics",
    "media": "C360Tracker",
    "real_estate": "C360Tracker",
    "travel": "C360Tracker",
}

DOMAIN_EVENT_CHANNEL = {
    "retail": "mobile_app",
    "education": "web",
    "media": "web",
    "real_estate": "web",
    "travel": "mobile_app",
}

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
    """Creates raw profiles for anonymous/unresolved events.

    Returns a dict mapping (domain, device_id) to raw_profile_id so events can reference them.
    """
    logger.info("Seeding raw profiles for anonymous events (travel/real_estate/media/education)...")
    raw_profile_map = {}
    rng = stable_rng("anonymous_raw_profiles")

    # Create raw profiles per unique unresolved-event domain.
    unresolved_domains = sorted({domain for domain, *_rest in UNRESOLVED_EVENTS})
    for domain in unresolved_domains:
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
                    raw_profile_id, DEMO_TENANT_ID, domain, "C360Tracker", "web",
                    device_id, "page-view", 1,
                ),
            )
            raw_profile_map[(domain, device_id)] = raw_profile_id

    return raw_profile_map


def seed_raw_events(cursor, master_profiles: list, raw_profile_map: dict | None = None) -> None:
    logger.info(
        "Seeding cdp_raw_events for all master profiles (minimum %d events/profile)...",
        MIN_EVENTS_PER_MASTER_PROFILE,
    )
    for m in master_profiles:
        rng = stable_rng(f"events:{m['master_profile_id']}")
        domain = canonical_demo_domain(m["domain"])
        catalog = DOMAIN_EVENT_CATALOG.get(domain, RETAIL_EVENTS)
        source_system = DOMAIN_EVENT_SOURCE_SYSTEM.get(domain, "C360Tracker")
        event_channel = DOMAIN_EVENT_CHANNEL.get(domain, "web")
        for category, event_name, value_range, entity_type, is_conversion in catalog:
            cursor.execute(
                f"""
                INSERT INTO {_table('cdp_raw_events')}
                    (tenant_id, domain, master_profile_id, raw_profile_id, source_system, channel, event_category,
                     event_name, is_conversion, entity_type, event_value, currency, event_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                """,
                (
                    DEMO_TENANT_ID, domain, m["master_profile_id"], m["first_seen_raw_profile_id"],
                    source_system,
                    event_channel, category, event_name, is_conversion, entity_type,
                    rng.randint(*value_range) if value_range else None, "VND",
                    datetime.now() - timedelta(days=realistic_event_days_ago(rng), hours=rng.randint(0, 23)),
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
                    DEMO_TENANT_ID, domain, m["master_profile_id"], m["first_seen_raw_profile_id"],
                    source_system,
                    event_channel, category, event_name, is_conversion, entity_type,
                    rng.randint(*value_range) if value_range else None, "VND",
                    datetime.now() - timedelta(days=realistic_event_days_ago(rng), hours=rng.randint(0, 23)),
                ),
            )

    logger.info("Seeding anonymous cdp_raw_events for travel/real_estate/media/education domains (no resolved profile yet)...")
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
                DEMO_TENANT_ID, domain, device_id, raw_profile_id, "C360Tracker", "web",
                category, event_name, is_conversion, entity_type,
                rng.randint(*value_range) if value_range else None, "VND",
                datetime.now() - timedelta(days=realistic_event_days_ago(rng, max_days=365)),
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
                        datetime.now() - timedelta(days=rng.randint(0, 365), hours=rng.randint(0, 23)),
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
    ("education_account_1", 0, "education", 0),
    ("education_account_2", 1, "education", 1),
    ("retail_account_1", 2, "retail", 0),
    ("retail_account_2", 3, "retail", 1),
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
    education = [m["master_profile_id"] for m in master_profiles if canonical_demo_domain(m["domain"]) == "education"]
    retail = [m["master_profile_id"] for m in master_profiles if canonical_demo_domain(m["domain"]) == "retail"]
    domain_pools = {"education": education, "retail": retail}
    contacts = crm_ids["contact"]
    contact_account_names = crm_ids.get("contact_account_names") or []

    metadata = Json({"demo_tenant": DEMO_TENANT_ID})
    links = []
    for _account_name, contact_index, domain, pool_index in CONTACT_MASTER_LINK_ACCOUNTS:
        pool = domain_pools.get(domain)
        if pool is None or contact_index >= len(contacts) or pool_index >= len(pool):
            continue
        account_name = (
            contact_account_names[contact_index]
            if contact_index < len(contact_account_names)
            else _account_name
        )
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
EDUCATION_CHANNELS = ("Learning Platform", "Mobile App", "Website", "Instructor Portal")
REAL_ESTATE_CHANNELS = ("Property Portal", "Mobile App", "Office Visit")
TRAVEL_CHANNELS = ("Airline App", "OTA Website", "Mobile App")
MEDIA_CHANNELS = ("Streaming App", "Website", "Mobile App")
LIFECYCLE_STAGES = ("prospect", "lead", "customer", "vip", "dormant", "churn_risk")
OCCUPATIONS = ("engineer", "teacher", "business_owner", "student", "civil_servant", "sales_professional")
INCOME_SEGMENTS = ("low", "medium", "high")
CITIES = ("Ho Chi Minh City", "Hanoi", "Da Nang", "Can Tho")

DOMAIN_PREFERRED_CHANNELS = {
    "retail": RETAIL_CHANNELS,
    "education": EDUCATION_CHANNELS,
    "real_estate": REAL_ESTATE_CHANNELS,
    "travel": TRAVEL_CHANNELS,
    "media": MEDIA_CHANNELS,
}

DOMAIN_CLV_CONFIG = {
    "retail": {"multiplier": 1, "high": 3000, "medium": 1000},
    "education": {"multiplier": 3, "high": 7000, "medium": 2500},
    "real_estate": {"multiplier": 5, "high": 25000, "medium": 8000},
    "travel": {"multiplier": 2, "high": 6000, "medium": 2000},
    "media": {"multiplier": 1, "high": 2500, "medium": 800},
}


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
        domain = canonical_demo_domain(m["domain"])
        rng = stable_rng(f"enrich:{master_id}")

        lifecycle_stage = rng.choice(LIFECYCLE_STAGES)
        is_established_customer = lifecycle_stage in ("customer", "vip", "dormant", "churn_risk")
        preferred_channel = rng.choice(DOMAIN_PREFERRED_CHANNELS.get(domain, EDUCATION_CHANNELS))
        # customer_since: back-date by 0-365 days for established customers (realistic year-over-year retention)
        customer_since = (
            (m["created_at"] - timedelta(days=rng.randint(0, 365))).date() if is_established_customer else None
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
        clv_config = DOMAIN_CLV_CONFIG.get(domain, DOMAIN_CLV_CONFIG["retail"])
        historical_clv = round(rng.uniform(500, 5000) * clv_config["multiplier"], 2)
        predictive_clv = round(historical_clv * rng.uniform(1.0, 1.8), 2)
        clv_high_threshold = clv_config["high"]
        clv_medium_threshold = clv_config["medium"]
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
        first_name, last_name, full_name, name_locale = build_global_profile_name(rng)
        gender = rng.choice(("male", "female", "other"))
        address = Json({"city": rng.choice(CITIES), "country": "VN"})
        profile_picture_url = f"https://api.dicebear.com/7.x/identicon/svg?seed={master_id}"
        persona_summary = _make_persona_summary(domain, lifecycle_stage, preferred_channel, rng)

        set_clauses = [
            "domain = %s", "lifecycle_stage = %s", "preferred_channel = %s", "customer_since = %s",
            "last_activity_at = %s", "churn_probability = %s", "churn_risk_tier = %s",
            "lead_conversion_probability = %s", "lead_grade = %s", "historical_clv = %s",
            "predictive_clv = %s", "clv_segment = %s", "engagement_score = %s",
            "latest_nps_score = %s", "average_csat = %s", "overall_sentiment_score = %s",
            "profile_completeness_score = %s", "identity_confidence_score = %s",
            "segmentation_tags = %s", "communication_preferences = COALESCE(communication_preferences, '{}'::jsonb) || %s",
            "attributes = COALESCE(attributes, '{}'::jsonb) || %s",
            "model_versions = %s", "scores_updated_at = NOW()", "gender = %s", "address = %s",
            "profile_picture_url = %s", "persona_summary = %s",
            "full_name = %s", "first_name = %s", "last_name = %s",
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
            domain, lifecycle_stage, preferred_channel, customer_since, last_activity_at,
            churn_probability, churn_risk_tier, lead_conversion_probability, lead_grade,
            historical_clv, predictive_clv, clv_segment, engagement_score, latest_nps_score,
            average_csat, overall_sentiment_score, profile_completeness_score,
            identity_confidence_score, segmentation_tags, communication_preferences, attributes, model_versions,
            gender, address, profile_picture_url, persona_summary,
            full_name, first_name, last_name,
        ]
        domain_attributes: dict[str, object] = {}

        if domain == "retail":
            email = f"{email_token(first_name)}.{email_token(last_name)}.{master_id[:8]}@example.com"
            phone_number = f"09{rng.randint(10000000, 99999999)}"
            set_clauses.extend([
                "email = %s", "phone_number = %s", "is_hashed = FALSE",
            ])
            params.extend([email, phone_number])

            domain_attributes = {
                "loyalty_id": f"LOY-{master_id[:8]}",
                "membership_tier": rng.choice(("Silver", "Gold", "Platinum")),
                "preferred_store_code": f"STORE-{rng.randint(1, 20):03d}",
            }
        elif domain == "education":
            completion_rate = round(rng.uniform(0.35, 0.98), 4)
            domain_attributes = {
                "student_id": f"STU-{rng.randint(100000, 999999)}",
                "institution_name": rng.choice(("Demo University", "Demo Online Academy", "Demo Polytechnic")),
                "learning_mode": rng.choice(("self_paced", "instructor_led", "hybrid")),
                "name_locale": name_locale,
                "course_completion_rate": completion_rate,
                "enrolled_programs": rng.sample(
                    ["Data Analytics Certificate", "AI Foundations", "Digital Marketing", "Business English"],
                    k=rng.randint(1, 2),
                ),
                "certification_goal": rng.choice(("none", "ielts", "aws", "pmp")),
            }
        elif domain == "real_estate":
            domain_attributes = {
                "property_types_of_interest": rng.sample(
                    ["apartment", "villa", "land", "townhouse", "condo"],
                    k=rng.randint(1, 3),
                ),
                "preferred_location_codes": [f"DIST-{rng.randint(1, 12):02d}" for _ in range(rng.randint(1, 2))],
            }
        elif domain == "travel":
            domain_attributes = {
                "travel_loyalty_program_id": f"TVL-{rng.randint(100000, 999999)}",
                "preferred_travel_class": rng.choice(("economy", "business", "first")),
            }
        elif domain == "media":
            domain_attributes = {
                "media_subscription_id": f"SUB-{rng.randint(100000, 999999)}",
                "preferred_content_genres": rng.sample(
                    ["news", "sports", "entertainment", "documentary", "music"],
                    k=rng.randint(1, 3),
                ),
            }
        else:
            # Catch-all for any future domain; do nothing domain-specific.
            pass

        # NOTE: persona_embedding lives on the SHARED cdp_persona_archetypes
        # row (not cdp_master_profiles or cdp_customer_personas) -- see
        # seed_customer_personas() below, which sets it for a representative
        # subset of computed archetypes.

        params.append(master_id)
        cursor.execute(
            f"UPDATE {_table('cdp_master_profiles')} SET {', '.join(set_clauses)} WHERE master_profile_id = %s;",
            tuple(params),
        )

        if domain_attributes:
            cursor.execute(
                f"""
                INSERT INTO {_table('cdp_domain_profiles')} (
                    tenant_id,
                    master_profile_id,
                    domain_id,
                    profile_name,
                    lifecycle_stage,
                    persona_name,
                    persona_summary,
                    engagement_score,
                    domain_attributes,
                    first_activity_at,
                    last_activity_at,
                    status_code,
                    created_at,
                    updated_at
                )
                VALUES (
                    %s,
                    %s,
                    (SELECT domain_id FROM {_table('sys_domain')} WHERE domain_code = %s LIMIT 1),
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    1,
                    NOW(),
                    NOW()
                )
                ON CONFLICT (master_profile_id, domain_id)
                DO UPDATE SET
                    profile_name = EXCLUDED.profile_name,
                    lifecycle_stage = EXCLUDED.lifecycle_stage,
                    persona_name = EXCLUDED.persona_name,
                    persona_summary = EXCLUDED.persona_summary,
                    engagement_score = EXCLUDED.engagement_score,
                    domain_attributes = COALESCE(cdp_domain_profiles.domain_attributes, '{{}}'::jsonb) || EXCLUDED.domain_attributes,
                    first_activity_at = EXCLUDED.first_activity_at,
                    last_activity_at = EXCLUDED.last_activity_at,
                    status_code = EXCLUDED.status_code,
                    updated_at = NOW();
                """,
                (
                    DEMO_TENANT_ID,
                    master_id,
                    domain,
                    f"{domain.title()} Profile",
                    lifecycle_stage,
                    None,
                    None,
                    engagement_score,
                    Json(domain_attributes),
                    customer_since,
                    last_activity_at,
                ),
            )



# --------------------------------------------------------------------------
# Persona archetypes ("Ideal Customer Profile" per sys_domain + product/time)
# --------------------------------------------------------------------------
# Two curated ICP archetypes per customer360 sys_domain (see
# database-init/init-core-database.sql's SYSTEM DOMAINS insert): a premium/
# high-value target and an emerging/growth target, each tied to a concrete
# product and campaign time window (product/period are folded into
# persona_name/persona_summary -- cdp_persona_archetypes has no dedicated
# product/period columns, and adding them isn't needed for this demo). The
# centroid_*_score fields are the ICP's DECLARED target profile (not derived
# from real data) -- what seed_customer_personas() below lookalike-matches
# each master profile's own computed component scores against.
ICP_ARCHETYPES = [
    # -- retail --
    {
        "domain": "retail", "persona_code": "retail_gen_z_sneaker_collector_2026h2",
        "persona_name": "Gen Z Sneaker Collector -- Q3-Q4 2026 Sneaker Drop",
        "persona_category": "Champion", "product": "Limited-Edition Sneaker Drops",
        "campaign_period": "2026-07-01 to 2026-12-31",
        "persona_summary": "ICP for the H2 2026 limited-edition sneaker drop campaign: highly engaged Gen Z shoppers who buy frequently via the mobile app and chase every new release.",
        "centroid": {"behavior": 85, "engagement": 90, "financial": 55, "loyalty": 65, "relationship": 55, "risk": 20},
    },
    {
        "domain": "retail", "persona_code": "retail_household_essentials_loyalist_2026h2",
        "persona_name": "Household Essentials Loyalist -- H2 2026 Subscribe & Save",
        "persona_category": "Growth Potential", "product": "Everyday Essentials Subscription",
        "campaign_period": "2026-07-01 to 2026-12-31",
        "persona_summary": "ICP for the H2 2026 Subscribe & Save program: steady repeat buyers of everyday essentials, moderate spend, strong loyalty-program participation.",
        "centroid": {"behavior": 60, "engagement": 55, "financial": 70, "loyalty": 85, "relationship": 60, "risk": 15},
    },
    # -- education --
    {
        "domain": "education", "persona_code": "education_enterprise_lms_sponsor_fy2026",
        "persona_name": "Enterprise LMS Sponsor -- FY2026",
        "persona_category": "Champion", "product": "Enterprise LMS + Outcome Analytics",
        "campaign_period": "2026-01-01 to 2026-12-31",
        "persona_summary": "ICP for FY2026 enterprise learning contracts: high-LTV organizations sponsoring multi-seat upskilling programs.",
        "centroid": {"behavior": 70, "engagement": 65, "financial": 92, "loyalty": 88, "relationship": 75, "risk": 8},
    },
    {
        "domain": "education", "persona_code": "education_mobile_first_exam_prep_2026",
        "persona_name": "Mobile-First Exam Prep Learner -- 2026",
        "persona_category": "Growth Potential", "product": "Digital Exam Prep + Mentorship",
        "campaign_period": "2026-01-01 to 2026-12-31",
        "persona_summary": "ICP for 2026 mobile exam-prep programs: younger learners with high app engagement and growing conversion potential.",
        "centroid": {"behavior": 80, "engagement": 85, "financial": 45, "loyalty": 50, "relationship": 40, "risk": 25},
    },
    # -- insurance --
    {
        "domain": "insurance", "persona_code": "insurance_family_protection_planner_2026",
        "persona_name": "Family Protection Planner -- 2026 Open Enrollment",
        "persona_category": "Champion", "product": "Family Life & Health Bundle",
        "campaign_period": "2026-09-01 to 2026-11-30",
        "persona_summary": "ICP for the 2026 open-enrollment family bundle campaign: established households bundling life + health coverage for dependents.",
        "centroid": {"behavior": 65, "engagement": 60, "financial": 70, "loyalty": 75, "relationship": 70, "risk": 20},
    },
    {
        "domain": "insurance", "persona_code": "insurance_young_professional_starter_2026",
        "persona_name": "Young Professional Starter Plan -- 2026",
        "persona_category": "Growth Potential", "product": "Term Life Starter Plan",
        "campaign_period": "2026-01-01 to 2026-12-31",
        "persona_summary": "ICP for the 2026 starter term-life plan: early-career professionals buying their first policy, low premium, still low engagement.",
        "centroid": {"behavior": 55, "engagement": 50, "financial": 45, "loyalty": 40, "relationship": 35, "risk": 30},
    },
    # -- healthcare --
    {
        "domain": "healthcare", "persona_code": "healthcare_chronic_care_patient_2026",
        "persona_name": "Chronic Care Management Patient -- 2026 Telehealth Program",
        "persona_category": "Champion", "product": "Chronic Care Telehealth Program",
        "campaign_period": "2026-01-01 to 2026-12-31",
        "persona_summary": "ICP for the 2026 chronic-care telehealth program: frequent, highly engaged patients requiring ongoing remote monitoring.",
        "centroid": {"behavior": 75, "engagement": 80, "financial": 55, "loyalty": 70, "relationship": 65, "risk": 35},
    },
    {
        "domain": "healthcare", "persona_code": "healthcare_preventive_wellness_seeker_2026",
        "persona_name": "Preventive Wellness Seeker -- 2026 Annual Checkup Package",
        "persona_category": "Growth Potential", "product": "Annual Wellness Checkup Package",
        "campaign_period": "2026-01-01 to 2026-12-31",
        "persona_summary": "ICP for the 2026 annual wellness checkup package: healthy, occasional visitors seeking preventive rather than acute care.",
        "centroid": {"behavior": 60, "engagement": 65, "financial": 50, "loyalty": 55, "relationship": 45, "risk": 10},
    },
    # -- telecom --
    {
        "domain": "telecom", "persona_code": "telecom_unlimited_data_power_user_2026q4",
        "persona_name": "Unlimited Data Power User -- Q4 2026 5G Family Plan",
        "persona_category": "Champion", "product": "Unlimited 5G Family Plan",
        "campaign_period": "2026-10-01 to 2026-12-31",
        "persona_summary": "ICP for the Q4 2026 unlimited 5G family plan launch: heavy data users on multi-line family accounts.",
        "centroid": {"behavior": 85, "engagement": 88, "financial": 60, "loyalty": 60, "relationship": 50, "risk": 18},
    },
    {
        "domain": "telecom", "persona_code": "telecom_budget_prepaid_user_2026",
        "persona_name": "Budget-Conscious Prepaid User -- 2026 Value Plan",
        "persona_category": "Growth Potential", "product": "Prepaid Value Plan",
        "campaign_period": "2026-01-01 to 2026-12-31",
        "persona_summary": "ICP for the 2026 prepaid value plan: price-sensitive, low-usage subscribers with light engagement.",
        "centroid": {"behavior": 40, "engagement": 35, "financial": 25, "loyalty": 30, "relationship": 20, "risk": 35},
    },
    # -- travel --
    {
        "domain": "travel", "persona_code": "travel_luxury_getaway_enthusiast_2026q4",
        "persona_name": "Luxury Getaway Enthusiast -- Q4 2026 Peak Season Package",
        "persona_category": "Champion", "product": "Premium All-Inclusive Getaway",
        "campaign_period": "2026-10-01 to 2026-12-31",
        "persona_summary": "ICP for the Q4 2026 peak-season premium getaway package: high-spend travelers booking all-inclusive luxury trips.",
        "centroid": {"behavior": 80, "engagement": 75, "financial": 88, "loyalty": 70, "relationship": 60, "risk": 10},
    },
    {
        "domain": "travel", "persona_code": "travel_budget_backpacker_explorer_2026",
        "persona_name": "Budget Backpacker Explorer -- 2026 City-Break Package",
        "persona_category": "Growth Potential", "product": "Budget City-Break Package",
        "campaign_period": "2026-01-01 to 2026-12-31",
        "persona_summary": "ICP for the 2026 budget city-break package: frequent but low-spend independent travelers.",
        "centroid": {"behavior": 70, "engagement": 60, "financial": 30, "loyalty": 35, "relationship": 30, "risk": 25},
    },
    # -- real_estate --
    {
        "domain": "real_estate", "persona_code": "real_estate_luxury_condo_investor_2026q4",
        "persona_name": "Luxury Condo Investor -- Q4 2026 Riverside Launch",
        "persona_category": "Champion", "product": "Riverside Luxury Condo Launch",
        "campaign_period": "2026-10-01 to 2026-12-31",
        "persona_summary": "ICP for the Q4 2026 riverside luxury condo launch: high-net-worth investors buying multiple premium units.",
        "centroid": {"behavior": 65, "engagement": 55, "financial": 95, "loyalty": 60, "relationship": 55, "risk": 12},
    },
    {
        "domain": "real_estate", "persona_code": "real_estate_first_time_homebuyer_2026",
        "persona_name": "First-Time Homebuyer -- 2026 Mortgage Program",
        "persona_category": "Growth Potential", "product": "First-Time Homebuyer Mortgage Program",
        "campaign_period": "2026-01-01 to 2026-12-31",
        "persona_summary": "ICP for the 2026 first-time homebuyer mortgage program: early-career buyers purchasing their first property.",
        "centroid": {"behavior": 55, "engagement": 60, "financial": 40, "loyalty": 45, "relationship": 40, "risk": 30},
    },
    # -- education --
    {
        "domain": "education", "persona_code": "education_career_upskiller_2026fall",
        "persona_name": "Career Upskiller Professional -- Fall 2026 Certificate Cohort",
        "persona_category": "Champion", "product": "Executive Data Analytics Certificate",
        "campaign_period": "2026-09-01 to 2026-12-15",
        "persona_summary": "ICP for the Fall 2026 executive data analytics certificate cohort: working professionals investing in career-advancing credentials.",
        "centroid": {"behavior": 75, "engagement": 80, "financial": 60, "loyalty": 55, "relationship": 50, "risk": 15},
    },
    {
        "domain": "education", "persona_code": "education_lifelong_learner_hobbyist_2026",
        "persona_name": "Lifelong Learner Hobbyist -- 2026 Enrichment Courses",
        "persona_category": "Growth Potential", "product": "Self-Paced Enrichment Courses",
        "campaign_period": "2026-01-01 to 2026-12-31",
        "persona_summary": "ICP for the 2026 self-paced enrichment catalog: casual learners taking low-stakes courses for personal interest.",
        "centroid": {"behavior": 50, "engagement": 45, "financial": 30, "loyalty": 40, "relationship": 35, "risk": 10},
    },
    # -- manufacturing --
    {
        "domain": "manufacturing", "persona_code": "manufacturing_enterprise_bulk_buyer_fy2026",
        "persona_name": "Enterprise B2B Bulk Buyer -- FY2026 Supply Contract",
        "persona_category": "Champion", "product": "Industrial Equipment Bulk Supply Contract",
        "campaign_period": "2026-01-01 to 2026-12-31",
        "persona_summary": "ICP for the FY2026 industrial equipment bulk-supply contract: large enterprise accounts with deep, long-tenure relationships.",
        "centroid": {"behavior": 70, "engagement": 60, "financial": 90, "loyalty": 80, "relationship": 85, "risk": 15},
    },
    {
        "domain": "manufacturing", "persona_code": "manufacturing_sme_growth_partner_2026",
        "persona_name": "SME Growth Partner -- 2026 Equipment Financing Plan",
        "persona_category": "Growth Potential", "product": "Modular Equipment Financing Plan",
        "campaign_period": "2026-01-01 to 2026-12-31",
        "persona_summary": "ICP for the 2026 modular equipment financing plan: small/mid-size manufacturers scaling up with financed equipment.",
        "centroid": {"behavior": 55, "engagement": 50, "financial": 55, "loyalty": 50, "relationship": 60, "risk": 25},
    },
    # -- media --
    {
        "domain": "media", "persona_code": "media_premium_streaming_bingewatcher_2026q3",
        "persona_name": "Premium Streaming Binge-Watcher -- Q3 2026 Ad-Free Launch",
        "persona_category": "Champion", "product": "Premium Ad-Free Streaming Tier",
        "campaign_period": "2026-07-01 to 2026-09-30",
        "persona_summary": "ICP for the Q3 2026 premium ad-free tier launch: daily, high-watch-time subscribers upgrading away from ads.",
        "centroid": {"behavior": 85, "engagement": 92, "financial": 55, "loyalty": 65, "relationship": 45, "risk": 15},
    },
    {
        "domain": "media", "persona_code": "media_casual_ad_supported_viewer_2026",
        "persona_name": "Casual Ad-Supported Viewer -- 2026 Basic Tier",
        "persona_category": "Growth Potential", "product": "Ad-Supported Basic Tier",
        "campaign_period": "2026-01-01 to 2026-12-31",
        "persona_summary": "ICP for the 2026 ad-supported basic tier: infrequent, price-sensitive viewers with light watch time.",
        "centroid": {"behavior": 40, "engagement": 40, "financial": 20, "loyalty": 30, "relationship": 20, "risk": 20},
    },
]


def seed_persona_archetypes(cursor) -> dict:
    """Upserts the curated ICP archetype catalog above into
    cdp_persona_archetypes (one row per tenant/domain/persona_code) and
    returns them grouped by domain, each carrying its persona_archetype_id
    and centroid vector, ready for seed_customer_personas()'s lookalike
    matching below. persona_embedding is seeded once per archetype here
    (deterministic from persona_code) -- it's the SHARED centroid embedding,
    not duplicated per matched profile."""
    logger.info("Seeding %d ICP persona archetypes across every sys_domain...", len(ICP_ARCHETYPES))
    archetypes_by_domain: dict[str, list] = {}
    for icp in ICP_ARCHETYPES:
        embedding_rng = stable_rng(f"persona_archetype_embedding:{icp['persona_code']}")
        vector_literal = (
            "[" + ",".join(f"{embedding_rng.uniform(-1, 1):.6f}" for _ in range(PERSONA_EMBEDDING_DIM)) + "]"
        )
        cursor.execute(
            f"""
            INSERT INTO {_table('cdp_persona_archetypes')} (
                tenant_id, domain, persona_code, persona_name, persona_category, persona_summary,
                llm_provider, llm_model, persona_embedding,
                centroid_behavior_score, centroid_engagement_score, centroid_financial_score,
                centroid_loyalty_score, centroid_relationship_score, centroid_risk_score
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector({PERSONA_EMBEDDING_DIM}), %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, domain, persona_code) DO UPDATE SET
                persona_name = EXCLUDED.persona_name,
                persona_category = EXCLUDED.persona_category,
                persona_summary = EXCLUDED.persona_summary,
                llm_provider = EXCLUDED.llm_provider,
                llm_model = EXCLUDED.llm_model,
                persona_embedding = EXCLUDED.persona_embedding,
                centroid_behavior_score = EXCLUDED.centroid_behavior_score,
                centroid_engagement_score = EXCLUDED.centroid_engagement_score,
                centroid_financial_score = EXCLUDED.centroid_financial_score,
                centroid_loyalty_score = EXCLUDED.centroid_loyalty_score,
                centroid_relationship_score = EXCLUDED.centroid_relationship_score,
                centroid_risk_score = EXCLUDED.centroid_risk_score,
                updated_at = NOW()
            RETURNING persona_archetype_id;
            """,
            (
                DEMO_TENANT_ID, icp["domain"], icp["persona_code"], icp["persona_name"],
                icp["persona_category"], icp["persona_summary"], "seed-script", "icp-catalog-v1",
                vector_literal,
                icp["centroid"]["behavior"], icp["centroid"]["engagement"], icp["centroid"]["financial"],
                icp["centroid"]["loyalty"], icp["centroid"]["relationship"], icp["centroid"]["risk"],
            ),
        )
        persona_archetype_id = cursor.fetchone()["persona_archetype_id"]
        archetypes_by_domain.setdefault(icp["domain"], []).append(
            {
                "persona_archetype_id": persona_archetype_id,
                "persona_name": icp["persona_name"],
                "persona_summary": icp["persona_summary"],
                "persona_category": icp["persona_category"],
                "centroid_behavior_score": icp["centroid"]["behavior"],
                "centroid_engagement_score": icp["centroid"]["engagement"],
                "centroid_financial_score": icp["centroid"]["financial"],
                "centroid_loyalty_score": icp["centroid"]["loyalty"],
                "centroid_relationship_score": icp["centroid"]["relationship"],
                "centroid_risk_score": icp["centroid"]["risk"],
            }
        )
    return archetypes_by_domain


def _cosine_similarity(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _lookalike_match(computation, archetypes: list):
    """Finds the ICP archetype (within the profile's own domain) whose
    centroid component-score vector this profile's OWN computed scores most
    resemble (cosine similarity) -- the lookalike model that decides
    cdp_customer_personas.persona_archetype_id + match_score."""
    profile_vector = [
        computation.behavior_score, computation.engagement_score, computation.financial_score,
        computation.loyalty_score, computation.relationship_score, computation.risk_score,
    ]
    best_archetype = None
    best_score = -1.0
    for archetype in archetypes:
        centroid_vector = [
            archetype["centroid_behavior_score"], archetype["centroid_engagement_score"],
            archetype["centroid_financial_score"], archetype["centroid_loyalty_score"],
            archetype["centroid_relationship_score"], archetype["centroid_risk_score"],
        ]
        similarity = _cosine_similarity(profile_vector, centroid_vector)
        if similarity > best_score:
            best_score = similarity
            best_archetype = archetype
    return best_archetype, round(max(best_score, 0.0), 4)


def seed_customer_personas(cursor, master_profiles: list, archetypes_by_domain: dict) -> int:
    """For every enriched master profile: computes its own six component
    scores via PersonaResolutionEngine's pure compute_persona() (behavior/
    engagement/financial/loyalty/relationship/risk + persona_score/
    confidence), lookalike-matches it against the ICP archetypes seeded for
    its domain (_lookalike_match), then persists a versioned
    cdp_customer_personas MATCH row referencing the winning
    persona_archetype_id -- via the SAME PersonaResolutionEngine persistence
    helpers (features/score-details/history/master-profile update) the CIR
    pipeline uses in production (resolver.py), instead of duplicating that
    SQL here.

    Idempotent / safe to re-run: each call inserts a fresh computed_version
    (deactivating the previous one), so re-running just adds another version
    rather than erroring.
    """
    logger.info(
        "Computing personas + lookalike-matching %d master profiles against %d ICP archetypes...",
        len(master_profiles), sum(len(v) for v in archetypes_by_domain.values()),
    )
    engine = PersonaResolutionEngine(schema=DB_SCHEMA)
    engine._ensure_runtime_persona_config(cursor)
    computed = 0
    unmatched_domains = set()

    for m in master_profiles:
        master_profile = engine._fetch_master_profile(cursor, DEMO_TENANT_ID, m["master_profile_id"])
        if master_profile is None:
            continue

        domain = master_profile.get("domain") or "retail"
        archetypes = archetypes_by_domain.get(domain)
        if not archetypes:
            unmatched_domains.add(domain)
            continue

        computation = compute_persona(master_profile)
        best_archetype, match_score = _lookalike_match(computation, archetypes)
        assert best_archetype is not None  # archetypes is non-empty here, so a match always exists
        computation.match_score = match_score
        # The profile's displayed persona identity is the ARCHETYPE it was
        # matched to, not an independently-generated name/summary.
        computation.persona_name = best_archetype["persona_name"]
        computation.persona_summary = best_archetype["persona_summary"]
        computation.persona_category = best_archetype["persona_category"]

        old_persona = engine._fetch_current_persona(cursor, DEMO_TENANT_ID, m["master_profile_id"])
        computed_version = engine._next_computed_version(
            cursor, DEMO_TENANT_ID, m["master_profile_id"], best_archetype["persona_archetype_id"]
        )
        engine._deactivate_previous_personas(cursor, DEMO_TENANT_ID, m["master_profile_id"])
        persona_id = engine._insert_persona(
            cursor, DEMO_TENANT_ID, domain, m["master_profile_id"], best_archetype["persona_archetype_id"],
            computation, computed_version,
        )
        engine._insert_features(cursor, persona_id, computation.features)
        engine._insert_score_details(cursor, persona_id, computation)
        if engine._should_insert_history(old_persona, computation):
            engine._insert_history(cursor, persona_id, old_persona, computation)
        engine._update_master_profile(cursor, DEMO_TENANT_ID, m["master_profile_id"], persona_id, computation)
        computed += 1

    if unmatched_domains:
        logger.warning(
            "No ICP archetypes seeded for domain(s) %s -- skipped persona matching for those profiles.",
            sorted(unmatched_domains),
        )
    logger.info("Computed %d personas, each lookalike-matched to a shared ICP archetype.", computed)
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
            set_tenant_context(cursor, DEMO_TENANT_ID)
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
            seed_data_sources(cursor)
            seed_scoring_models(cursor)
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
            archetypes_by_domain = seed_persona_archetypes(cursor)
            personas_computed = seed_customer_personas(cursor, master_profiles, archetypes_by_domain)
            seed_content_items(cursor, master_profiles)
            link_crm_contacts_to_master_profiles(cursor, crm_ids, master_profiles)

        conn.commit()
        logger.info(
            "Full demo data seeded: %d master profiles enriched, %d ICP persona archetypes seeded "
            "across sys_domain, %d got detail rows (relations/contacts/transactions); all master "
            "profiles got >= %d events; content items: %d/profile/type; %d customer personas "
            "computed + lookalike-matched to an ICP archetype; CRM journey graph + "
            "graph_edges + cdp_relation_types seeded; crm_contact <-> cdp_master_profiles linked "
            "via graph_edges ('is_active_as') + cross-referenced attributes/metadata.",
            len(master_profiles), len(ICP_ARCHETYPES), len(detail_profiles),
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
