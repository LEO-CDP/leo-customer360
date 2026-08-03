"""Generate demo data for fuzzy matching (Address/Company) Identity Resolution.

This script creates synthetic raw profiles with intentional fuzzy-matching
variations:
  - Name variations: "Ng Minh" vs "Nguyễn Minh", "Corp" vs "Corporation"
  - Address variations: "123 Main St" vs "123 Main Street", typos, abbreviations
  - Company name variations: "ACME Corp" vs "Acme Corporation", "Foo Inc" vs "Foo Incorporated"

The identity resolver should fuzzy-match these similar-but-not-identical
values using the pg_trgm similarity() function (threshold 0.6 for address,
0.65 for company).

Safe to re-run: idempotent upsert by tenant_id + email/phone.
"""

import hashlib
import logging
import os
import random
from datetime import datetime, timedelta

import psycopg2
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_NAME = os.environ.get("DB_NAME", "customer360")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "password")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_SCHEMA = os.environ.get("DB_SCHEMA", "customer360")

DEMO_TENANT_ID = "11111111-1111-1111-1111-111111111111"

HASHED_PII_FIELDS = ("full_name", "email", "phone_number", "national_id")


def hash_pii(value):
    """SHA-256 hash of normalized PII."""
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# Vietnamese cities (major ones)
CITIES = ("Ho Chi Minh", "Hanoi", "Da Nang", "Can Tho", "Hai Phong")
STATES = ("Ho Chi Minh", "Ha Noi", "Da Nang", "Bac Ninh", "Bac Giang")
POSTAL_CODES = ("700000", "100000", "550000", "900000", "031000")

# Address variations for fuzzy matching (main street addresses)
ADDRESS_BASE_TEMPLATES = [
    # Template: (line1, line2_optional, city)
    ("123 Le Loi Boulevard", "District 1", "Ho Chi Minh"),
    ("456 Nguyen Hue Street", "District 1", "Ho Chi Minh"),
    ("789 Tran Hung Dao Avenue", "District 2", "Ho Chi Minh"),
    ("321 Ly Tu Trong Street", "District 1", "Ho Chi Minh"),
    ("654 Pasteur Street", "District 1", "Ho Chi Minh"),
    ("111 Hang Gai Street", None, "Hanoi"),
    ("222 Trang Tien Street", None, "Hanoi"),
    ("333 Ba Trieu Street", None, "Hanoi"),
]

# Address fuzzy-match variations (abbreviations, typos, slight changes)
ADDRESS_VARIATIONS = {
    "123 Le Loi Boulevard": [
        "123 Le Loi Blvd",  # abbreviation
        "123 Le Loi Blvd.",  # with period
        "123 Le Loi Bvd",  # typo
        "123 Le Loi Avenue",  # synonymy
    ],
    "456 Nguyen Hue Street": [
        "456 Nguyen Hue St",
        "456 Nguyen Hue St.",
        "456 Nguyen Hue Str",  # typo
    ],
    "789 Tran Hung Dao Avenue": [
        "789 Tran Hung Dao Ave",
        "789 Tran Hung Dao Avenue",
        "789 Trần Hung Đạo Avenue",  # accented spelling
    ],
    "321 Ly Tu Trong Street": [
        "321 Ly Tu Trong St",
        "321 Ly Tu Trong Street",
    ],
    "654 Pasteur Street": [
        "654 Pasteur St",
        "654 Pasteur Street",
    ],
}

# Company variations for fuzzy matching
COMPANY_BASE_NAMES = [
    "Acme Corporation",
    "Tech Solutions Inc",
    "Financial Services Ltd",
    "Digital Marketing Agency",
    "Global Trade Company",
]

COMPANY_VARIATIONS = {
    "Acme Corporation": [
        "Acme Corp",
        "ACME Corp",
        "Acme Corp.",
        "Acme Corpotation",  # typo
        "Acme Co",
    ],
    "Tech Solutions Inc": [
        "Tech Solutions Incorporated",
        "Tech Solutions",
        "Tech Sol Inc",
    ],
    "Financial Services Ltd": [
        "Financial Services Limited",
        "Financial Svcs Ltd",
        "Financial Services",
    ],
    "Digital Marketing Agency": [
        "Digital Marketing",
        "Digital Mktg Agency",
    ],
    "Global Trade Company": [
        "Global Trade Co",
        "Global Trade",
        "GlobalTrade Company",  # missing space
    ],
}


def generate_fuzzy_match_profiles(num_groups: int = 5) -> list[dict]:
    """Generate synthetic profiles with address/company fuzzy-match variations.
    
    For each "group", creates:
    - 1 canonical profile (perfect data)
    - N variations (fuzzy-match targets) via different source systems
    
    This tests whether the resolver correctly fuzzy-matches these variations
    into a single master profile, despite slight differences in address/company.
    """
    profiles = []
    rng = random.Random(42)  # Seeded for reproducibility
    
    for group_idx in range(num_groups):
        # Pick a base address and company
        base_addr, addr_line2, city = rng.choice(ADDRESS_BASE_TEMPLATES)
        base_company = rng.choice(COMPANY_BASE_NAMES)
        
        # Generate a stable identity for this group
        full_name = f"Test Customer {group_idx:03d}"
        email = f"test_customer_{group_idx:03d}@example.com"
        phone = f"09{1000000 + group_idx:08d}"
        
        # Create canonical profile (AppsFlyer install, no PII yet)
        profiles.append({
            "source_system": "AppsFlyer",
            "channel": "mobile_app",
            "domain": "retail",
            "device_id": f"device-fuzzy-{group_idx:03d}-001",
            "advertising_id": f"idfa-fuzzy-{group_idx:03d}-001",
            "platform": "ios",
            "app_version": "4.0.0",
            "media_source": "Facebook Ads",
            "campaign": "fuzzy_match_test",
            "event_name": "install",
            "event_time": datetime.now() - timedelta(days=30),
            # No PII on install
        })
        
        # Variation 1: AppsFlyer login touch - canonical address/company
        profiles.append({
            "source_system": "AppsFlyer",
            "channel": "mobile_app",
            "domain": "retail",
            "device_id": f"device-fuzzy-{group_idx:03d}-001",
            "advertising_id": f"idfa-fuzzy-{group_idx:03d}-001",
            "platform": "ios",
            "full_name": full_name,
            "email": email,
            "phone_number": phone,
            "national_id": f"{rng.randint(10, 99)}{1000000000 + group_idx:09d}",
            "address_line1": base_addr,
            "address_line2": addr_line2,
            "city": city,
            "state_province": rng.choice(STATES),
            "postal_code": rng.choice(POSTAL_CODES),
            "country": "Vietnam",
            "company_name": base_company,
            "event_name": "login",
            "event_time": datetime.now() - timedelta(days=20),
        })
        
        # Variation 2: MoEngage push engagement - fuzzy address variant
        addr_variant = rng.choice(ADDRESS_VARIATIONS.get(base_addr, [base_addr]))
        company_variant = rng.choice(COMPANY_VARIATIONS.get(base_company, [base_company]))
        profiles.append({
            "source_system": "MoEngage",
            "channel": "mobile_app",
            "domain": "retail",
            "device_id": f"device-fuzzy-{group_idx:03d}-001",  # Same device (cross-source key)
            "push_token": f"fcm-fuzzy-{group_idx:03d}",
            "platform": "ios",
            "full_name": full_name,
            "email": email,
            "phone_number": phone,
            "national_id": f"{rng.randint(10, 99)}{1000000000 + group_idx:09d}",
            "address_line1": addr_variant,  # FUZZY variant
            "address_line2": addr_line2,
            "city": city,
            "state_province": rng.choice(STATES),
            "postal_code": rng.choice(POSTAL_CODES),
            "country": "Vietnam",
            "company_name": company_variant,  # FUZZY variant
            "event_name": "push_engagement",
            "event_time": datetime.now() - timedelta(days=15),
        })
        
        # Variation 3: Web Tracking - another fuzzy variant
        addr_variant2 = rng.choice(ADDRESS_VARIATIONS.get(base_addr, [base_addr]))
        company_variant2 = rng.choice(COMPANY_VARIATIONS.get(base_company, [base_company]))
        profiles.append({
            "source_system": "WebTracking",
            "channel": "web",
            "domain": "retail",
            "cookie_id": f"cookie-fuzzy-{group_idx:03d}",
            "ga_client_id": f"GA1.2.{1000000000 + group_idx}.{2000000000 + group_idx}",
            "full_name": full_name,
            "email": email,
            "phone_number": phone,
            "national_id": f"{rng.randint(10, 99)}{1000000000 + group_idx:09d}",
            "address_line1": addr_variant2,  # DIFFERENT fuzzy variant
            "address_line2": addr_line2,
            "city": city,
            "state_province": rng.choice(STATES),
            "postal_code": rng.choice(POSTAL_CODES),
            "country": "Vietnam",
            "company_name": company_variant2,  # DIFFERENT fuzzy variant
            "utm_source": "direct",
            "utm_medium": "none",
            "event_name": "page_view",
            "event_time": datetime.now() - timedelta(days=10),
        })
    
    return profiles


def seed_fuzzy_demo_profiles(cursor, raw_profiles: list[dict]) -> None:
    """Insert fuzzy-match demo profiles into cdp_raw_profiles_stage."""
    logger.info("Inserting %d fuzzy-match demo raw profiles...", len(raw_profiles))
    
    # Columns include new address/company fields
    columns = (
        "tenant_id", "domain", "source_system", "channel", "external_customer_id",
        "full_name", "first_name", "last_name", "email", "phone_number", "national_id",
        "date_of_birth", "address_line1", "address_line2", "city", "state_province",
        "postal_code", "country", "company_name",
        "device_id", "advertising_id", "platform", "app_version", "push_token",
        "cookie_id", "ga_client_id", "session_id", "media_source", "campaign",
        "utm_source", "utm_medium", "utm_campaign", "event_name", "event_time"
    )
    
    placeholders = ", ".join(["%s"] * len(columns))
    insert_query = f"""
        INSERT INTO {_table('cdp_raw_profiles_stage')} ({", ".join(columns)})
        VALUES ({placeholders});
    """
    
    for profile in raw_profiles:
        values = [DEMO_TENANT_ID]
        for col in columns[1:]:
            value = profile.get(col)
            if col in HASHED_PII_FIELDS:
                value = hash_pii(value)
            values.append(value)
        
        try:
            cursor.execute(insert_query, values)
        except Exception as e:
            logger.error(f"Error inserting profile: {e}")
            raise


def _table(name: str) -> str:
    return f"{DB_SCHEMA}.{name}" if DB_SCHEMA else name


def main() -> None:
    """Generate and seed fuzzy-match demo data."""
    try:
        conn = psycopg2.connect(
            host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, port=DB_PORT
        )
        cursor = conn.cursor()
        
        # Generate profiles
        raw_profiles = generate_fuzzy_match_profiles(num_groups=10)
        logger.info(f"Generated {len(raw_profiles)} fuzzy-match demo profiles")
        
        # Seed them
        seed_fuzzy_demo_profiles(cursor, raw_profiles)
        conn.commit()
        
        logger.info("✓ Fuzzy-match demo data seeded successfully")
        logger.info(f"  Total profiles: {len(raw_profiles)}")
        logger.info(f"  Expected master profiles after resolution: 10 (one per group)")
        logger.info("  Address fuzzy-match threshold: 0.60")
        logger.info("  Company fuzzy-match threshold: 0.65")
        
    except Exception as e:
        logger.error(f"Error: {e}")
        raise
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
