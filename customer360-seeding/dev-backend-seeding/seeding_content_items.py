"""Seed the demo ``cdp_content_items`` catalog.

This is the single owner of demo content-item seeding. The catalog covers the
supported Customer 360 profile domains: travel, media, hospitality, retail,
real estate, healthcare, and education.

The rows are shared domain content, not one copy per profile. Recommendation
ranking uses ``segment_tags`` overlap with profile segmentation tags, so every
row is tagged with its domain plus useful audience/topic tags.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import psycopg2
from dotenv import load_dotenv

from leo_customer360_dao.utils.tenant_context import set_tenant_context

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_NAME = os.environ.get("DB_NAME", "customer360")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "password")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_SCHEMA = os.environ.get("DB_SCHEMA", "customer360")

DEMO_TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEMO_NAMESPACE = uuid.UUID("12345678-1234-5678-1234-567812345678")


SUPPORTED_CONTENT_DOMAINS = (
    "travel",
    "media",
    "hospitality",
    "retail",
    "real_estate",
    "healthcare",
    "education",
)
CONTENT_ITEM_TYPES = frozenset({"news", "video", "product", "article"})
CONTENT_CATALOG_PATH = Path(__file__).with_name("seeding_demo_contents.json")


def load_content_catalog(path: Path = CONTENT_CATALOG_PATH) -> tuple[Mapping[str, Any], ...]:
    """Load and validate the shared recommendation catalog from JSON."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Content catalog must be a JSON array: {path}")

    catalog: list[Mapping[str, Any]] = []
    seen_slugs: set[str] = set()
    domain_counts = dict.fromkeys(SUPPORTED_CONTENT_DOMAINS, 0)
    required_fields = {
        "slug", "domain", "item_type", "title", "summary", "image_url",
        "cta_label", "cta_url", "segment_tags",
    }
    for index, raw_item in enumerate(payload):
        if not isinstance(raw_item, dict):
            raise ValueError(f"Content catalog item {index} must be an object")
        missing_fields = required_fields - raw_item.keys()
        if missing_fields:
            raise ValueError(
                f"Content catalog item {index} is missing: {sorted(missing_fields)}"
            )

        item = dict(raw_item)
        slug = item["slug"]
        domain = item["domain"]
        item_type = item["item_type"]
        if not all(
            isinstance(item[field], str) and item[field].strip()
            for field in required_fields - {"segment_tags"}
        ):
            raise ValueError(f"Content catalog item {index} has an empty string field")
        if slug in seen_slugs:
            raise ValueError(f"Duplicate content catalog slug: {slug}")
        if domain not in SUPPORTED_CONTENT_DOMAINS:
            raise ValueError(f"Unsupported content catalog domain: {domain}")
        if item_type not in CONTENT_ITEM_TYPES:
            raise ValueError(f"Unsupported content catalog item type: {item_type}")
        if not isinstance(item["segment_tags"], list) or not item["segment_tags"] or not all(
            isinstance(tag, str) and tag.strip() for tag in item["segment_tags"]
        ):
            raise ValueError(f"Content catalog item {index} must have non-empty string segment_tags")

        item["segment_tags"] = tuple(item["segment_tags"])
        catalog.append(item)
        seen_slugs.add(slug)
        domain_counts[domain] += 1

    insufficient_domains = {
        domain: count for domain, count in domain_counts.items() if count < 7
    }
    if insufficient_domains:
        raise ValueError(
            f"Content catalog requires at least 7 items per domain: {insufficient_domains}"
        )
    return tuple(catalog)


CONTENT_CATALOG = load_content_catalog()


def _table(name: str) -> str:
    return f"{DB_SCHEMA}.{name}" if DB_SCHEMA else name


def _content_item_id(slug: str) -> str:
    return str(uuid.uuid5(DEMO_NAMESPACE, f"content-item:{slug}"))


def reset_content_items(cursor) -> None:
    """Remove only this demo tenant's content catalog."""
    cursor.execute(
        f"DELETE FROM {_table('cdp_content_items')} WHERE tenant_id = %s;",
        (DEMO_TENANT_ID,),
    )


def seed_content_items(cursor) -> int:
    """Reset and seed content for all supported demo domains."""
    reset_content_items(cursor)
    published_at = datetime.now(timezone.utc)
    for index, item in enumerate(CONTENT_CATALOG):
        item_published_at = published_at - timedelta(days=(index * 9) % 180)
        cursor.execute(
            f"""
            INSERT INTO {_table('cdp_content_items')}
                (content_item_id, tenant_id, domain, item_type, title, summary,
                 image_url, cta_label, cta_url, segment_tags, published_at, status_code)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1);
            """,
            (
                _content_item_id(str(item["slug"])),
                DEMO_TENANT_ID,
                item["domain"],
                item["item_type"],
                item["title"],
                item["summary"],
                item["image_url"],
                item["cta_label"],
                item["cta_url"],
                list(item["segment_tags"]),
                item_published_at,
            ),
        )
    logger.info(
        "Seeded %d cdp_content_items for the supported demo domains.",
        len(CONTENT_CATALOG),
    )
    return len(CONTENT_CATALOG)


def main() -> None:
    """Seed the content catalog for the fixed demo tenant."""
    conn = psycopg2.connect(
        host=DB_HOST,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        port=DB_PORT,
    )
    try:
        with conn.cursor() as cursor:
            set_tenant_context(cursor, DEMO_TENANT_ID)
            seed_content_items(cursor)
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Failed to seed cdp_content_items.")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()