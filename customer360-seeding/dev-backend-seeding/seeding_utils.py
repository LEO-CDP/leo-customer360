"""Validated demo metadata and reusable deterministic seeding helpers."""

from __future__ import annotations

import hashlib
import math
import os
import random
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote_plus, urlparse

from pydantic import BaseModel, ConfigDict, Field

DEMO_TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEMO_NAMESPACE = uuid.UUID("12345678-1234-5678-1234-567812345678")
METADATA_PATH = Path(__file__).with_name("seed_demo_metadata.json")

Campaign = tuple[str, str, str, str, str, str, int, int, int, str, str]
Transaction = tuple[str, str, str, str, tuple[int, int], str]
PerformanceProfile = tuple[tuple[int, int], float, float, int]
EventTemplate = tuple[str, str, str, bool, str]


class MetadataModel(BaseModel):
    """Reject misspelled or unsupported metadata fields."""

    model_config = ConfigDict(extra="forbid")


class DataSourceEnvironment(MetadataModel):
    """Environment overrides retained outside the static catalog."""

    url: str
    thumbnail: str
    url_fallback: str | None = None
    hosts: str | None = None
    generate_qr: bool = False


class DataSourceMetadata(MetadataModel):
    """Static source fixture and its optional runtime configuration."""

    name: str
    slug: str
    source_type: int
    status: int
    data_source_url: str | None
    thumbnail_url: str | None = None
    collect_directly: bool
    first_party_data: bool
    journey_level: int
    journey_map_id: str
    touchpoint_hub_id: str
    security_code: str
    total_tracked_event: int
    avg_daily_event: float
    avg_events_per_profile: float
    access_tokens: dict[str, str]
    data_source_hosts: list[str]
    javascript_tags: list[str]
    qr_code_data: dict[str, Any] = Field(default_factory=dict)
    environment: DataSourceEnvironment

    def runtime_values(self) -> dict[str, Any]:
        """Apply URL, thumbnail, host, and QR overrides to a source fixture."""
        result = self.model_dump(exclude={"environment"})
        config = self.environment
        fallback = os.environ.get(config.url_fallback) if config.url_fallback else self.data_source_url
        source_url = configured_url(config.url, fallback)
        result["data_source_url"] = source_url
        result["thumbnail_url"] = configured_url(config.thumbnail, self.thumbnail_url)
        if config.hosts:
            result["data_source_hosts"] = configured_hosts(config.hosts, source_url, self.data_source_hosts)
        if config.generate_qr:
            result["qr_code_data"] = configured_qr_code_data(source_url, self.slug)
        return result


class AgentMetadata(MetadataModel):
    """Demo AI-agent registry row."""

    agent_code: str
    display_name: str
    description: str
    model_type: str
    status: str
    schedule_definition: str
    input_features: list[str]
    hyperparameters: dict[str, Any]


class ArchetypeMetadata(MetadataModel):
    """Demo persona archetype with its declared component-score centroid."""

    domain: str
    persona_code: str
    persona_name: str
    persona_category: str
    product: str
    campaign_period: str
    persona_summary: str
    centroid: dict[str, float]


class ClvMetadata(MetadataModel):
    """Domain-specific demo CLV distribution."""

    multiplier: int
    high: int
    medium: int


class DemoMetadata(MetadataModel):
    """Typed catalogs loaded from the JSON file shipped beside the seeder."""

    behavioral_source_slugs: dict[str, str]
    behavioral_event_templates: dict[str, list[EventTemplate]]
    industries: list[tuple[str, str]]
    accounts: list[tuple[str, str]]
    lead_sources: list[tuple[str, str]]
    campaigns: list[Campaign]
    lead_first_names: tuple[str, ...]
    lead_last_names: tuple[str, ...]
    vn_profile_first_names: tuple[str, ...]
    vn_profile_last_names: tuple[str, ...]
    eu_profile_first_names: tuple[str, ...]
    eu_profile_last_names: tuple[str, ...]
    us_profile_first_names: tuple[str, ...]
    us_profile_last_names: tuple[str, ...]
    platform_profile: dict[str, PerformanceProfile]
    default_profile: PerformanceProfile
    data_sources: list[DataSourceMetadata]
    ai_agent_models: list[AgentMetadata]
    domain_transaction_catalog: dict[str, list[Transaction]]
    contact_master_link_accounts: tuple[tuple[str, int, str, int], ...]
    domain_preferred_channels: dict[str, tuple[str, ...]]
    lifecycle_stages: tuple[str, ...]
    occupations: tuple[str, ...]
    income_segments: tuple[str, ...]
    cities: tuple[str, ...]
    domain_clv_config: dict[str, ClvMetadata]
    icp_archetypes: list[ArchetypeMetadata]
    relation_types: list[tuple[str, str]]
    persona_summary_flavors: list[str]
    contact_definitions: list[tuple[str, int, int]]
    opportunity_definitions: list[tuple[str, int, str, int, str, int]]


def load_demo_metadata(path: Path = METADATA_PATH) -> DemoMetadata:
    """Load and validate all catalogs; propagate missing or invalid metadata."""
    return DemoMetadata.model_validate_json(path.read_text(encoding="utf-8"))


def table_name(schema: str, name: str) -> str:
    """Qualify a table with validated SQL identifiers."""
    for identifier in (schema, name):
        if identifier and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
            raise ValueError(f"Invalid SQL identifier: {identifier!r}")
    if not name:
        raise ValueError("Table name must not be empty")
    return f"{schema}.{name}" if schema else name


def demo_id(key: str) -> str:
    """Return the stable UUID used for a demo entity key."""
    return str(uuid.uuid5(DEMO_NAMESPACE, key))


def stable_rng(key: str) -> random.Random:
    """Create a reproducible RNG without modifying global random state."""
    seed = int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16) % (2**32)
    return random.Random(seed)


def canonical_demo_domain(domain: str | None) -> str:
    """Preserve the existing retail fallback for missing demo domains."""
    return domain or "retail"


def realistic_event_days_ago(rng: random.Random, max_days: int = 365) -> int:
    """Spread historical events across quarters with a recent-activity bias."""
    if max_days < 1:
        raise ValueError("max_days must be positive")
    quarter = max(1, max_days // 4)
    bucket = rng.random()
    if bucket < 0.30:
        return rng.randint(1, min(quarter, max_days))
    if bucket < 0.55:
        return rng.randint(min(quarter + 1, max_days), min(quarter * 2, max_days))
    if bucket < 0.78:
        return rng.randint(min(quarter * 2 + 1, max_days), min(quarter * 3, max_days))
    return rng.randint(min(quarter * 3 + 1, max_days), max_days)


def build_global_profile_name(
    rng: random.Random, metadata: DemoMetadata
) -> tuple[str, str, str, str]:
    """Choose a synthetic VN/EU/US name from the configured name pools."""
    locale = rng.choices(("vn", "eu", "us"), weights=(0.35, 0.35, 0.30), k=1)[0]
    if locale == "vn":
        first_name = rng.choice(metadata.vn_profile_first_names)
        last_name = rng.choice(metadata.vn_profile_last_names)
        full_name = f"{last_name} {first_name}"
    elif locale == "eu":
        first_name = rng.choice(metadata.eu_profile_first_names)
        last_name = rng.choice(metadata.eu_profile_last_names)
        full_name = f"{first_name} {last_name}"
    else:
        first_name = rng.choice(metadata.us_profile_first_names)
        last_name = rng.choice(metadata.us_profile_last_names)
        full_name = f"{first_name} {last_name}"
    return first_name, last_name, full_name, locale


def email_token(value: str) -> str:
    """Normalize a synthetic name for its demo email local part."""
    token = "".join(ch.lower() if ch.isalnum() else "." for ch in value)
    while ".." in token:
        token = token.replace("..", ".")
    return token.strip(".")


def tracking_platform_for_campaign(platform: str) -> str:
    """Map campaign platform labels to their tracking-source names."""
    return {
        "Adjust": "adjust",
        "Google": "ga4",
        "C360Tracker": "c360_tracker",
    }.get(platform, platform.lower().replace(" ", "_"))


def configured_url(name: str, fallback: str | None = None) -> str | None:
    """Read a URL override; an explicitly blank value disables the URL."""
    value = os.environ.get(name)
    return fallback if value is None else value.strip() or None


def configured_hosts(name: str, data_source_url: str | None, fallback: list[str]) -> list[str]:
    """Resolve hosts from an override, source URL, or catalog fallback."""
    value = os.environ.get(name)
    if value is not None:
        return [host.strip() for host in value.split(",") if host.strip()]
    hostname = urlparse(data_source_url or "").hostname
    return [hostname] if hostname else fallback


def configured_qr_code_data(data_source_url: str | None, slug: str) -> dict[str, str]:
    """Generate QR tracking metadata for the configured source URL."""
    if not data_source_url:
        return {}
    separator = "&" if "?" in data_source_url else "?"
    tracking_url = (
        f"{data_source_url}{separator}utm_source={slug}"
        "&utm_medium=qr_code&utm_campaign=c360_datasource"
    )
    return {
        "target_url": data_source_url,
        "tracking_url": tracking_url,
        "qr_code_url": (
            "https://api.qrserver.com/v1/create-qr-code/?size=250x250&data="
            f"{quote_plus(tracking_url)}"
        ),
        "generated_at": datetime.now().isoformat(),
    }


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Calculate similarity for equal-dimensional vectors, including zeros."""
    if len(a) != len(b):
        raise ValueError("Vectors must have equal dimensions")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def behavioral_event_hour(event_time: datetime) -> str:
    """Return the UTC event-lake partition hour."""
    return event_time.astimezone(timezone.utc).strftime("%Y-%m-%d-%H")


def behavioral_object_key(source_id: str, event_hour: str) -> str:
    """Return the existing demo event-object key."""
    return f"events/{event_hour}/demo-behavioral-{source_id}.jsonl.gz"