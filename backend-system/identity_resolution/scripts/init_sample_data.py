"""Seeds sample test data for a Customer Identity Resolution (CIR) demo run.

Connects to the PostgreSQL database configured via environment variables /
``.env`` (see ``daily_job.py``) and:

1. Ensures the ``pg_trgm`` / ``fuzzystrmatch`` extensions are enabled (needed
   for the ``fuzzy_dmetaphone`` matching rule used by other attributes).
2. Ensures the ``cdp_profile_attributes`` (full attribute catalog + CIR
   matching-rule metadata, now part of ``core-customer360/database-schema.sql``)
   and ``cdp_id_resolution_status`` (real-time throttle state, still a CIR
   runtime-only table) exist. The ``CREATE TABLE IF NOT EXISTS`` here is only
   a defensive fallback for databases where ``database-schema.sql`` has not
   been (re)applied yet; it is a no-op once that schema has been migrated in.
3. Seeds/upserts the active identity-resolution matching rules (only the
   matching-rule-specific columns -- the full attribute metadata is owned by
   ``database-schema.sql``'s seed data).
4. Ensures a ``sys_tenant`` row exists for ``DEMO_TENANT_ID`` (idempotent
   upsert-if-missing) -- ``database-schema.sql`` never seeds tenants, and
   every tenant-scoped table has a NOT NULL FK to ``sys_tenant.tenant_id``.
5. Clears any previous demo data (scoped to ``DEMO_TENANT_ID`` only, so it
   never touches other tenants) and inserts 1,000 generated raw profiles for
   both the banking and retail domains: each customer's anonymous first
   touch is an AppsFlyer install (across Facebook Ads, TikTok Ads, Google
   Ads, Grab Ads, FPT Play Ads, and offline PR events at shopping malls),
   and ~30% of the rows are additional PII-revealing duplicate touches
   round-robined across all 3 documented source systems -- AppsFlyer,
   MoEngage (push engagement), and Web Tracking (browser/GA4-style) -- for
   identity resolution to merge back into a single master profile per
   customer. See generate_raw_profiles().

No Personal Data (PII) is ever written to the database: ``full_name``,
``email``, ``phone_number`` and ``national_id`` are one-way SHA-256 hashed
(normalized/trimmed/lowercased first) before insertion -- the same pattern
used by real-world hashed-match integrations (e.g. Meta/Google Customer
Match). Matching still works because identical inputs always hash to the
same value. ``full_name`` is hashed/stored for display like the other PII
fields but is NOT an identity-resolution matching key (``is_identity_resolution
= FALSE`` in ``init-core-database.sql``) -- common/shared names are too
collision-prone to safely decide two raw profiles are the same person.

Safe to re-run: every step is idempotent / scoped to ``DEMO_TENANT_ID``.
"""

import hashlib
import logging
import os
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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

# Fixed tenant used for the demo so this script is safe to re-run: only rows
# belonging to this tenant are ever deleted/re-seeded.
DEMO_TENANT_ID = "11111111-1111-1111-1111-111111111111"

# Raw-stage/master-profile columns that hold Personal Data (PII). Values for
# these columns are SHA-256 hashed before ever being written to the
# database -- see hash_pii() below.
HASHED_PII_FIELDS = ("full_name", "email", "phone_number", "national_id")


def hash_pii(value):
    """Returns a SHA-256 hex digest of a normalized PII value, or None.

    Normalizes (trim + lowercase) before hashing so equivalent raw values
    (e.g. the same email reported by two different source systems) always
    collide to the same hash, preserving identity-resolution matching
    without ever storing the plaintext PII.
    """
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# --- Synthetic multi-source (AppsFlyer / MoEngage / Web Tracking) data ------
#
# AppsFlyer channel/media_source configuration: Facebook Ads, TikTok Ads,
# Google Ads, Grab Ads, FPT Play Ads (video/OTT), and offline PR events
# (e.g. roadshows / registration booths at shopping malls) that still get
# attributed back into the app via AppsFlyer OneLink QR codes.
APPSFLYER_CHANNELS = [
    {"media_source": "Facebook Ads", "campaigns": ["vn_retail_fb_flash_sale", "vn_bank123_creditcard_fb_q4"]},
    {"media_source": "TikTok Ads", "campaigns": ["vn_tiktok_genz_promo", "vn_tiktok_livestream_sale"]},
    {"media_source": "Google Ads", "campaigns": ["vn_google_search_brand", "vn_google_display_retargeting"]},
    {"media_source": "Grab Ads", "campaigns": ["vn_grab_inapp_banner", "vn_grab_loyalty_crosspromo"]},
    {"media_source": "FPT Play Ads", "campaigns": ["vn_fptplay_video_preroll", "vn_fptplay_channel_sponsorship"]},
    {"media_source": "Offline PR Event", "campaigns": ["hcmc_shopping_mall_roadshow", "hanoi_mall_activation_day"]},
]

# MoEngage push/engagement campaigns (mobile app engagement touches).
MOENGAGE_CAMPAIGNS = [
    "push_flash_sale_reminder", "push_cart_abandonment", "push_loyalty_points_expiry", "push_kyc_reminder",
]

# Web Tracking (browser/GA4-style) UTM sources for the same customers logging
# in / browsing on the web instead of the mobile app.
WEBTRACKING_UTM_SOURCES = [
    {"utm_source": "google", "utm_medium": "cpc", "utm_campaign": "vn_google_search_brand"},
    {"utm_source": "facebook", "utm_medium": "social", "utm_campaign": "vn_retail_fb_flash_sale"},
    {"utm_source": "newsletter", "utm_medium": "email", "utm_campaign": "vn_retail_email_promo"},
    {"utm_source": "direct", "utm_medium": "none", "utm_campaign": None},
]

# The 3 documented ingestion source systems (see resolver.py's module
# docstring / README) that raw touch events are spread across.
TOUCH_SOURCE_SYSTEMS = ("AppsFlyer", "MoEngage", "WebTracking")

VIETNAMESE_FAMILY_NAMES = (
    "Nguyen", "Tran", "Le", "Pham", "Hoang", "Huynh", "Phan", "Vu", "Vo", "Dang", "Bui", "Do", "Ho", "Ngo", "Duong",
)
VIETNAMESE_MIDDLE_NAMES = ("Van", "Thi", "Huu", "Minh", "Ngoc", "Thanh", "Quang", "Xuan")
VIETNAMESE_GIVEN_NAMES = (
    "An", "Binh", "Chi", "Dung", "Giang", "Ha", "Hoa", "Huong", "Khanh", "Lan", "Linh",
    "Long", "Mai", "Nam", "Nga", "Phuong", "Quan", "Son", "Thao", "Thu", "Trang", "Tuan", "Yen",
)

RETAIL_TOUCH_EVENTS = ("login", "app_open", "purchase")
BANKING_TOUCH_EVENTS = ("login", "kyc_completed", "loan_application")

# Share of synthetic customers whose app belongs to the banking domain
# (the rest are retail). Tune here if you want a different domain mix.
BANKING_DOMAIN_SHARE = 0.4


def _random_full_name(rng: random.Random) -> str:
    return " ".join(
        [rng.choice(VIETNAMESE_FAMILY_NAMES), rng.choice(VIETNAMESE_MIDDLE_NAMES), rng.choice(VIETNAMESE_GIVEN_NAMES)]
    )


def _build_customer(rng: random.Random, index: int, used_names: set, used_phones: set) -> dict:
    """Creates one synthetic person's stable identity (device + PII), shared
    across every raw-profile row generated for that person.

    phone_number is regenerated on collision since it IS an active CIR
    matching rule -- a coincidental collision would make identity resolution
    incorrectly merge two distinct people into one profile. full_name is also
    kept collision-free purely for demo realism/readability; it is NOT a CIR
    matching key (common/shared names are too collision-prone to trust for
    identity matching -- see init-core-database.sql's is_identity_resolution
    seed for full_name).
    """
    domain = "banking" if rng.random() < BANKING_DOMAIN_SHARE else "retail"
    platform = "ios" if rng.random() < 0.5 else "android"
    channel = rng.choice(APPSFLYER_CHANNELS)

    full_name = _random_full_name(rng)
    while full_name in used_names:
        full_name = _random_full_name(rng)
    used_names.add(full_name)

    phone_number = f"09{rng.randint(10000000, 99999999)}"
    while phone_number in used_phones:
        phone_number = f"09{rng.randint(10000000, 99999999)}"
    used_phones.add(phone_number)

    email_slug = full_name.lower().replace(" ", ".")

    return {
        "domain": domain,
        "platform": platform,
        "media_source": channel["media_source"],
        "campaign": rng.choice(channel["campaigns"]),
        "device_id": f"device-{index:05d}-{rng.randint(1000, 9999)}",
        "advertising_id": f"af-{'idfa' if platform == 'ios' else 'gaid'}-{index:05d}",
        "external_customer_id": f"appsflyer_cust_{index:05d}",
        # MoEngage: same person's push-engagement identity (own per-source
        # customer id + push token; matched back via shared PII/device_id).
        "moengage_customer_id": f"moengage_cust_{index:05d}",
        "push_token": f"fcm-push-{index:05d}-{rng.randint(1000, 9999)}",
        "moengage_campaign": rng.choice(MOENGAGE_CAMPAIGNS),
        # Web Tracking: same person's browser identity (own visitor id,
        # cookie/GA client id, UTM attribution; matched back via shared PII).
        "webtracking_visitor_id": f"webtracking_visitor_{index:05d}",
        "cookie_id": f"cookie-{index:05d}-{rng.randint(1000, 9999)}",
        "ga_client_id": f"GA1.2.{rng.randint(1_000_000_000, 9_999_999_999)}.{rng.randint(1_000_000_000, 9_999_999_999)}",
        "utm": rng.choice(WEBTRACKING_UTM_SOURCES),
        "full_name": full_name,
        "email": f"{email_slug}{index}@example.com",
        "phone_number": phone_number,
        "national_id": (
            f"{rng.randint(10, 99)}{rng.randint(1000000000, 9999999999)}" if domain == "banking" else None
        ),
    }


def _install_event(customer: dict, event_time: datetime) -> dict:
    """First AppsFlyer touch: an anonymous install -- no PII revealed yet,
    only the device/advertising id and acquisition channel."""
    return {
        "domain": customer["domain"],
        "source_system": "AppsFlyer",
        "channel": "mobile_app",
        "device_id": customer["device_id"],
        "advertising_id": customer["advertising_id"],
        "platform": customer["platform"],
        "app_version": "3.4.2",
        "media_source": customer["media_source"],
        "campaign": customer["campaign"],
        "event_name": "install",
        "event_time": event_time,
    }


def _touch_event(rng: random.Random, customer: dict, event_time: datetime, source_system: str) -> dict:
    """A later touch (login/purchase/kyc/...) on the SAME customer that
    reveals PII, attributed to one of the 3 ingested source systems
    (AppsFlyer / MoEngage / WebTracking -- see TOUCH_SOURCE_SYSTEMS). Each
    source contributes its own per-source identifier (device_id/
    advertising_id, push_token, cookie_id/ga_client_id respectively), but
    shares the SAME full_name/email/phone_number/national_id AND device_id --
    the shared device_id (a valid cross-source identity-graph key per
    init-core-database.sql's device_id source_priority list, which already
    names WebTracking/AppsFlyer/MoEngage) is what guarantees every touch
    matches back to the SAME master profile regardless of the (shuffled)
    processing order; relying on the matching-key PII fields alone (email/
    phone_number/national_id -- full_name is NOT a matching key) would let a
    WebTracking/MoEngage touch reach a still-anonymous install row's master
    profile out of order and fragment into a second master profile that
    later collides on email.
    """
    events = BANKING_TOUCH_EVENTS if customer["domain"] == "banking" else RETAIL_TOUCH_EVENTS
    base = {
        "domain": customer["domain"],
        "source_system": source_system,
        "full_name": customer["full_name"],
        "email": customer["email"],
        "phone_number": customer["phone_number"],
        "national_id": customer["national_id"],
        "device_id": customer["device_id"],
        "event_name": rng.choice(events),
        "event_time": event_time,
    }
    if source_system == "AppsFlyer":
        base.update({
            "channel": "mobile_app",
            "external_customer_id": customer["external_customer_id"],
            "advertising_id": customer["advertising_id"],
            "platform": customer["platform"],
            "app_version": "3.4.2",
            "media_source": customer["media_source"],
            "campaign": customer["campaign"],
        })
    elif source_system == "MoEngage":
        base.update({
            "channel": "push",
            "external_customer_id": customer["moengage_customer_id"],
            "push_token": customer["push_token"],
            "platform": customer["platform"],
            "campaign": customer["moengage_campaign"],
        })
    else:  # WebTracking
        utm = customer["utm"]
        base.update({
            "channel": "web",
            "external_customer_id": customer["webtracking_visitor_id"],
            "cookie_id": customer["cookie_id"],
            "ga_client_id": customer["ga_client_id"],
            "utm_source": utm["utm_source"],
            "utm_medium": utm["utm_medium"],
            "utm_campaign": utm["utm_campaign"],
        })
    return base


def generate_raw_profiles(count: int = 1000, duplicate_rate: float = 0.3, seed: int = 42) -> list[dict]:
    """Generates ``count`` synthetic raw-profile events for the retail and
    banking domains, spread across all 3 documented ingestion source systems
    (AppsFlyer mobile attribution, MoEngage push engagement, Web Tracking/
    GA4-style browsing -- see TOUCH_SOURCE_SYSTEMS).

    Every customer's anonymous first touch is an AppsFlyer ``install`` event
    (device/advertising id only, no PII yet). ~``duplicate_rate`` of the rows
    are additional PII-revealing touches on that same customer, round-robined
    across AppsFlyer/MoEngage/WebTracking (own per-source identifier each,
    e.g. device_id/advertising_id, push_token, cookie_id/ga_client_id) --
    real duplicate profiles that identity resolution is expected to merge
    back into a single master profile whose ``source_systems`` ends up
    containing all 3 values.

    Uses a fixed ``seed`` so re-running this script produces the exact same
    dataset (consistent with the rest of this script being idempotent).
    """
    rng = random.Random(seed)
    num_unique = max(1, round(count * (1 - duplicate_rate)))
    num_duplicates = count - num_unique

    base_time = datetime.now() - timedelta(days=60)
    used_names: set = set()
    used_phones: set = set()
    customers = [_build_customer(rng, i, used_names, used_phones) for i in range(num_unique)]
    profiles = [_install_event(customer, base_time + timedelta(minutes=i)) for i, customer in enumerate(customers)]

    for i in range(num_duplicates):
        customer = rng.choice(customers)
        touch_time = base_time + timedelta(days=rng.randint(1, 45), hours=rng.randint(0, 23), minutes=rng.randint(0, 59))
        # Round-robin (not rng.choice) guarantees all 3 source systems are
        # actually represented in the seeded data, not just "likely".
        source_system = TOUCH_SOURCE_SYSTEMS[i % len(TOUCH_SOURCE_SYSTEMS)]
        profiles.append(_touch_event(rng, customer, touch_time, source_system))

    rng.shuffle(profiles)
    source_counts = {s: sum(1 for p in profiles if p["source_system"] == s) for s in ("AppsFlyer", "MoEngage", "WebTracking")}
    logger.info(
        "Generated %d raw profiles (%d unique customers, %d duplicate touches, ~%.0f%% duplicate rate). "
        "Per-source counts: %s",
        len(profiles), num_unique, num_duplicates, duplicate_rate * 100, source_counts,
    )
    return profiles


RAW_COLUMNS = (
    "tenant_id", "domain", "source_system", "channel", "external_customer_id",
    "full_name", "email", "phone_number", "national_id", "device_id",
    "advertising_id", "platform", "app_version", "push_token", "cookie_id",
    "ga_client_id", "session_id", "media_source", "campaign", "utm_source",
    "utm_medium", "utm_campaign", "event_name",
)


def _table(name: str) -> str:
    return f"{DB_SCHEMA}.{name}" if DB_SCHEMA else name


def ensure_extensions(cursor) -> None:
    logger.info("Ensuring pg_trgm / fuzzystrmatch extensions are enabled...")
    cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    cursor.execute("CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;")


def ensure_demo_tenant(cursor) -> None:
    """Ensures the demo tenant row exists in sys_tenant.

    database-schema.sql defines sys_tenant but never seeds any rows into it
    (it's RBAC data, not reference/vocab data), and every tenant-scoped table
    (cdp_raw_profiles_stage, cdp_master_profiles, ...) has a NOT NULL FK to
    sys_tenant.tenant_id. Without this, a fresh/reset database has no row
    for DEMO_TENANT_ID and seed_raw_profiles() below fails with
    ForeignKeyViolation. Idempotent: ON CONFLICT DO NOTHING so re-running
    this script never overwrites a customized demo tenant row.
    """
    logger.info("Ensuring demo tenant %s exists in sys_tenant...", DEMO_TENANT_ID)
    cursor.execute(
        f"""
        INSERT INTO {_table('sys_tenant')}
            (tenant_id, tenant_code, tenant_name, company_name, business_type, status)
        VALUES (%s, 'demo', 'Demo Tenant', 'Demo Company', 'retail_banking', 'ACTIVE')
        ON CONFLICT (tenant_id) DO NOTHING;
        """,
        (DEMO_TENANT_ID,),
    )





def reset_demo_data(cursor) -> None:
    """Deletes any previous demo data, scoped strictly to DEMO_TENANT_ID.

    Also clears every OTHER table that FK-references cdp_master_profiles /
    cdp_raw_profiles_stage and gets populated by
    scripts/seed_full_demo_data.py (cdp_raw_events, crm_transactions,
    crm_customer_contacts, cdp_relations) -- those must be deleted BEFORE
    cdp_master_profiles/cdp_raw_profiles_stage or re-running this script
    after seed_full_demo_data.py has run raises a ForeignKeyViolation (the
    old master profiles are still referenced from those tables).
    """
    logger.info("Resetting previous demo data for tenant_id=%s...", DEMO_TENANT_ID)
    cursor.execute(
        f"DELETE FROM {_table('cdp_raw_events')} WHERE tenant_id = %s;", (DEMO_TENANT_ID,)
    )
    cursor.execute(
        f"DELETE FROM {_table('crm_transactions')} WHERE tenant_id = %s;", (DEMO_TENANT_ID,)
    )
    cursor.execute(
        f"DELETE FROM {_table('crm_customer_contacts')} WHERE tenant_id = %s;", (DEMO_TENANT_ID,)
    )
    cursor.execute(
        f"DELETE FROM {_table('cdp_relations')} WHERE tenant_id = %s;", (DEMO_TENANT_ID,)
    )
    cursor.execute(
        f"DELETE FROM {_table('cdp_profile_links')} WHERE tenant_id = %s;", (DEMO_TENANT_ID,)
    )
    cursor.execute(
        f"DELETE FROM {_table('cdp_master_profiles')} WHERE tenant_id = %s;", (DEMO_TENANT_ID,)
    )
    cursor.execute(
        f"DELETE FROM {_table('cdp_raw_profiles_stage')} WHERE tenant_id = %s;", (DEMO_TENANT_ID,)
    )


def seed_raw_profiles(cursor, raw_profiles: list[dict]) -> None:
    """Inserts the sample raw profiles, SHA-256 hashing every PII field
    (see HASHED_PII_FIELDS) so no plaintext name/email/phone/national_id is
    ever written to the database."""
    logger.info("Inserting %d sample raw profiles (AppsFlyer / MoEngage / WebTracking)...", len(raw_profiles))
    columns = ", ".join(RAW_COLUMNS)
    placeholders = ", ".join(["%s"] * len(RAW_COLUMNS))
    insert_query = f"""
        INSERT INTO {_table('cdp_raw_profiles_stage')} ({columns})
        VALUES ({placeholders});
    """
    for profile in raw_profiles:
        values = []
        for col in RAW_COLUMNS[1:]:
            value = profile.get(col)
            if col in HASHED_PII_FIELDS:
                value = hash_pii(value)
            values.append(value)
        row = [DEMO_TENANT_ID] + values
        cursor.execute(insert_query, row)


def main() -> None:
    conn = psycopg2.connect(
        host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, port=DB_PORT
    )
    try:
        raw_profiles = generate_raw_profiles(count=1000, duplicate_rate=0.3)
        with conn.cursor() as cursor:
            set_tenant_context(cursor, DEMO_TENANT_ID)
            ensure_extensions(cursor)
            ensure_demo_tenant(cursor)
            reset_demo_data(cursor)
            seed_raw_profiles(cursor, raw_profiles)
        conn.commit()
        logger.info(
            "Sample data ready: %d raw profiles staged with status_code=1 for tenant_id=%s.",
            len(raw_profiles),
            DEMO_TENANT_ID,
        )
    except Exception:
        conn.rollback()
        logger.exception("Failed to seed sample data.")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
