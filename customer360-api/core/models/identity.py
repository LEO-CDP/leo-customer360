"""Customer Identity Resolution (CIR) core models.

Mirrors the ``MASTER PROFILES & IDENTITY RESOLUTION`` section of
core-customer360/database-schema.sql: ``cdp_master_profiles`` (golden
record), ``cdp_raw_profiles_stage`` (AppsFlyer/MoEngage/Web Tracking landing
zone) and ``cdp_profile_links`` (raw -> master links).

``CdpProfileAttribute`` is the full attribute catalog for
``cdp_master_profiles`` + ``cdp_domain_profiles`` (identity / demographic /
cross-channel graph / marketing / lineage columns plus Lead / Churn / CLV /
CX / Data Quality scoring-model metadata) and also carries the CIR
matching-rule and
consolidation metadata consumed by backend-system/identity_resolution's
``CustomerIdentityResolver``.
``CdpIdResolutionStatus`` (real-time throttle state) remains a CIR
*runtime-only* table, created idempotently by
backend-system/identity_resolution/scripts/init_sample_data.py
(``CREATE TABLE IF NOT EXISTS``).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, SmallInteger, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base


class CdpMasterProfile(Base):
    """The golden, resolved customer record (one per real-world person/tenant/domain)."""

    __tablename__ = "cdp_master_profiles"

    master_profile_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id"), nullable=False
    )
    # Data owner: internal sys_user who created/manages this profile (nullable -- most
    # profiles are created by ingestion pipelines, not an interactive admin user).
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_user.user_id"))
    domain: Mapped[str] = mapped_column(Text, nullable=False, server_default="retail")

    full_name: Mapped[Optional[str]] = mapped_column(Text)
    first_name: Mapped[Optional[str]] = mapped_column(Text)
    last_name: Mapped[Optional[str]] = mapped_column(Text)
    # True if full_name/email/phone_number and any domain-level identifier (for
    # example national_id in cdp_domain_profiles.domain_attributes) are SHA-256
    # hashed. Whenever TRUE,
    # persona_name must be populated (enforced by a DB CHECK constraint + identity-resolution-
    # service's persona.py, which auto-generates persona_name for hashed profiles).
    is_hashed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    email: Mapped[Optional[str]] = mapped_column(Text)
    phone_number: Mapped[Optional[str]] = mapped_column(Text)
    secondary_emails: Mapped[Optional[list]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    secondary_phones: Mapped[Optional[list]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date)
    gender: Mapped[Optional[str]] = mapped_column(Text)
    address: Mapped[Optional[dict]] = mapped_column(JSONB)
    company_name: Mapped[Optional[str]] = mapped_column(Text)

    external_ids: Mapped[Optional[dict]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    device_ids: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text), server_default=text("ARRAY[]::text[]"))
    advertising_ids: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text), server_default=text("ARRAY[]::text[]"))
    cookie_ids: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text), server_default=text("ARRAY[]::text[]"))
    push_tokens: Mapped[Optional[dict]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))

    acquisition_source: Mapped[Optional[str]] = mapped_column(Text)
    acquisition_campaign: Mapped[Optional[str]] = mapped_column(Text)
    # Points at the latest (is_active=TRUE) cdp_customer_personas row for this
    # profile. Nullable + ON DELETE SET NULL: computed asynchronously by
    # backend-system/identity_resolution's PersonaResolutionEngine
    # (persona_engine.py), never required at profile-creation time.
    current_persona_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("cdp_customer_personas.persona_id", ondelete="SET NULL")
    )
    # Human-readable, non-PII label required whenever is_hashed = TRUE (see persona.py).
    persona_name: Mapped[Optional[str]] = mapped_column(Text)
    segmentation_tags: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))
    communication_preferences: Mapped[Optional[dict]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    attributes: Mapped[Optional[dict]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    source_systems: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text), server_default=text("ARRAY[]::text[]"))
    first_seen_raw_profile_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True))

    # ------------------------------------------------------------------
    # Customer lifecycle & engagement tracking (prospect -> lead -> customer).
    # ------------------------------------------------------------------
    customer_since: Mapped[Optional[date]] = mapped_column(Date)
    # Updated continuously by the streaming/event pipeline (not batch).
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=False))
    preferred_channel: Mapped[Optional[str]] = mapped_column(Text)
    # 'prospect' | 'lead' | 'customer' | 'vip' | 'dormant' | 'churn_risk'
    lifecycle_stage: Mapped[Optional[str]] = mapped_column(Text)
    # Longer narrative summary, usually LLM/segmentation-pipeline generated (complements persona_name).
    persona_summary: Mapped[Optional[str]] = mapped_column(Text)

    # ------------------------------------------------------------------
    # ML & Analytics scoring models (Lead, Churn, CLV, CX, Data Quality).
    # ------------------------------------------------------------------
    lead_conversion_probability: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4))
    lead_grade: Mapped[Optional[str]] = mapped_column(Text)

    churn_probability: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4))
    churn_risk_tier: Mapped[Optional[str]] = mapped_column(Text)

    historical_clv: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), server_default="0.00")
    predictive_clv: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    clv_segment: Mapped[Optional[str]] = mapped_column(Text)

    engagement_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    latest_nps_score: Mapped[Optional[int]] = mapped_column(Integer)
    average_csat: Mapped[Optional[Decimal]] = mapped_column(Numeric(3, 2))
    overall_sentiment_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4))

    profile_completeness_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    identity_confidence_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4))

    model_versions: Mapped[Optional[dict]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    scores_updated_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=False))

    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(server_default=text("now()"))
    # 1: active, 0: inactive, -1: delete
    status_code: Mapped[int] = mapped_column(SmallInteger, server_default="1")


class CdpRawProfileStage(Base):
    """Landing zone for inbound AppsFlyer / MoEngage / Web Tracking / CoreBanking / POS events."""

    __tablename__ = "cdp_raw_profiles_stage"

    raw_profile_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id"), nullable=False
    )
    # Data owner: internal sys_user who created/manages this row (nullable -- rows are
    # normally landed by ingestion pipelines, not an interactive admin user).
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_user.user_id"))
    domain: Mapped[str] = mapped_column(Text, nullable=False, server_default="retail")
    source_system: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[Optional[str]] = mapped_column(Text)

    external_customer_id: Mapped[Optional[str]] = mapped_column(Text)
    full_name: Mapped[Optional[str]] = mapped_column(Text)
    first_name: Mapped[Optional[str]] = mapped_column(Text)
    last_name: Mapped[Optional[str]] = mapped_column(Text)
    email: Mapped[Optional[str]] = mapped_column(Text)
    phone_number: Mapped[Optional[str]] = mapped_column(Text)
    national_id: Mapped[Optional[str]] = mapped_column(Text)
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date)

    # Address fields for physical address fuzzy matching
    address_line1: Mapped[Optional[str]] = mapped_column(Text)
    address_line2: Mapped[Optional[str]] = mapped_column(Text)
    city: Mapped[Optional[str]] = mapped_column(Text)
    state_province: Mapped[Optional[str]] = mapped_column(Text)
    postal_code: Mapped[Optional[str]] = mapped_column(Text)
    country: Mapped[Optional[str]] = mapped_column(Text)
    company_name: Mapped[Optional[str]] = mapped_column(Text)

    device_id: Mapped[Optional[str]] = mapped_column(Text)
    advertising_id: Mapped[Optional[str]] = mapped_column(Text)
    platform: Mapped[Optional[str]] = mapped_column(Text)
    app_version: Mapped[Optional[str]] = mapped_column(Text)
    push_token: Mapped[Optional[str]] = mapped_column(Text)
    cookie_id: Mapped[Optional[str]] = mapped_column(Text)
    ga_client_id: Mapped[Optional[str]] = mapped_column(Text)
    session_id: Mapped[Optional[str]] = mapped_column(Text)
    ip_address: Mapped[Optional[str]] = mapped_column(INET)
    user_agent: Mapped[Optional[str]] = mapped_column(Text)

    media_source: Mapped[Optional[str]] = mapped_column(Text)
    campaign: Mapped[Optional[str]] = mapped_column(Text)
    utm_source: Mapped[Optional[str]] = mapped_column(Text)
    utm_medium: Mapped[Optional[str]] = mapped_column(Text)
    utm_campaign: Mapped[Optional[str]] = mapped_column(Text)

    event_name: Mapped[Optional[str]] = mapped_column(Text)
    event_time: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    event_payload: Mapped[Optional[dict]] = mapped_column(JSONB)

    status_code: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    processed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=text("now()"))


class CdpDomainProfile(Base):
    """Business-domain-specific attributes/persona attached to a master profile."""

    __tablename__ = "cdp_domain_profiles"
    __table_args__ = (
        UniqueConstraint("master_profile_id", "domain_id", name="uq_cdp_domain_profiles"),
    )

    domain_profile_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id"), nullable=False
    )
    master_profile_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("cdp_master_profiles.master_profile_id", ondelete="CASCADE"), nullable=False
    )
    domain_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sys_domain.domain_id"), nullable=False
    )

    profile_name: Mapped[Optional[str]] = mapped_column(Text)
    lifecycle_stage: Mapped[Optional[str]] = mapped_column(Text)
    persona_name: Mapped[Optional[str]] = mapped_column(Text)
    persona_summary: Mapped[Optional[str]] = mapped_column(Text)
    engagement_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))

    domain_attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    analytics: Mapped[Optional[dict]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))

    first_activity_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=False))
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=False))
    status_code: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), nullable=False, server_default=text("now()"))


class CdpProfileLink(Base):
    """Links a raw profile to the master profile it was resolved into."""

    __tablename__ = "cdp_profile_links"
    __table_args__ = (
        # Mirrors UNIQUE(tenant_id, raw_profile_id) in database-schema.sql.
        UniqueConstraint("tenant_id", "raw_profile_id", name="cdp_profile_links_tenant_id_raw_profile_id_key"),
    )

    link_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id"), nullable=False
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_user.user_id"))
    raw_profile_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("cdp_raw_profiles_stage.raw_profile_id"), nullable=False
    )
    master_profile_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("cdp_master_profiles.master_profile_id"), nullable=False
    )
    match_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4))
    match_method: Mapped[Optional[str]] = mapped_column(Text)
    # Link lifecycle state: ACTIVE | HISTORICAL | UNLINKED | SUPERSEDED (unmerge/profile-split).
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="ACTIVE")
    unlinked_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    unlinked_reason: Mapped[Optional[str]] = mapped_column(Text)
    unlinked_by: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_user.user_id"))
    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=text("now()"))


class CdpProfileAttribute(Base):
    """Full attribute catalog for cdp_master_profiles PLUS domain-specific
    attributes stored as JSONB keys in cdp_domain_profiles.domain_attributes
    (source_table='cdp_domain_profiles', master_profile_column=NULL -- e.g.
    national_id, kyc_status, loyalty_id), plus CIR matching-rule metadata
    consumed by CustomerIdentityResolver and ML scoring-model metadata
    (Lead / Churn / CLV / CX / Data Quality / persona risk & loyalty scores).

    attribute_group values include SYSTEM, IDENTITY, IDENTITY_GRAPH, RETAIL,
    BANKING, REAL_ESTATE, TRAVEL, MEDIA, EDUCATION, MARKETING, LINEAGE,
    LIFECYCLE, *_SCORING, DATA_QUALITY, GENERAL."""

    __tablename__ = "cdp_profile_attributes"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    attribute_internal_code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    master_profile_column: Mapped[Optional[str]] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    attribute_group: Mapped[str] = mapped_column(Text, nullable=False, server_default="GENERAL")
    source_table: Mapped[str] = mapped_column(Text, nullable=False, server_default="cdp_master_profiles")
    status: Mapped[Optional[str]] = mapped_column(Text, server_default="ACTIVE")
    data_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="TEXT")
    domain_scope: Mapped[str] = mapped_column(Text, nullable=False, server_default="all")
    is_pii: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    is_segmentable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    is_identity_resolution: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    matching_rule: Mapped[Optional[str]] = mapped_column(Text)
    matching_threshold: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4))
    consolidation_rule: Mapped[Optional[str]] = mapped_column(Text)
    consolidation_config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    # Rank hierarchy used during limit demotion (1 = highest priority, e.g. external_customer_id).
    priority_rank: Mapped[int] = mapped_column(Integer, nullable=False, server_default="99")
    # Maximum allowed unique values on a single master profile for this identifier.
    value_limit: Mapped[int] = mapped_column(Integer, nullable=False, server_default="5")
    # Window for limit enforcement: 1_ever, 5_weekly, 5_monthly, 5_annually.
    limit_timeframe: Mapped[str] = mapped_column(Text, nullable=False, server_default="5_annually")
    # Exact string values blocked from being promoted to external identifiers.
    blocked_values: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text('\'["null", "-1", "anonymous", "void", "abc123"]\'::jsonb')
    )
    # Regex patterns blocked from being promoted to external identifiers.
    blocked_patterns: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(Text), server_default=text("ARRAY['^[0-]*$']")
    )

    is_scoring_model: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    scoring_model_name: Mapped[Optional[str]] = mapped_column(Text)
    scoring_model_version: Mapped[Optional[str]] = mapped_column(Text)
    value_type: Mapped[Optional[str]] = mapped_column(Text)
    value_min: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    value_max: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    refresh_frequency: Mapped[Optional[str]] = mapped_column(Text)

    display_order: Mapped[int] = mapped_column(server_default="0")
    created_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))


class CdpIdentityIndex(Base):
    """Flattened O(1) lookup table mapping (tenant_id, identifier_type,
    identifier_value_normalized) -> master_profile_id, avoiding JSONB/array
    scans on cdp_master_profiles during streaming CIR match resolution."""

    __tablename__ = "cdp_identity_index"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "identifier_type", "identifier_value_normalized", name="uq_cdp_identity_index"
        ),
    )

    identity_index_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id"), nullable=False
    )
    master_profile_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("cdp_master_profiles.master_profile_id"), nullable=False
    )
    identifier_type: Mapped[str] = mapped_column(Text, nullable=False)
    identifier_value: Mapped[str] = mapped_column(Text, nullable=False)
    identifier_value_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    first_seen_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))


class CdpProfileMergeHistory(Base):
    """Audit log of master-to-master profile merges, storing JSONB snapshots
    of both sides so a bad merge can be unmerged/rolled back later."""

    __tablename__ = "cdp_profile_merge_history"

    merge_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id"), nullable=False
    )
    target_master_profile_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("cdp_master_profiles.master_profile_id"), nullable=False
    )
    # Merged/tombstoned profile id -- no FK, the row no longer exists after the merge.
    source_master_profile_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    merge_reason: Mapped[str] = mapped_column(Text, nullable=False)
    matched_identifier_type: Mapped[Optional[str]] = mapped_column(Text)
    matched_identifier_value: Mapped[Optional[str]] = mapped_column(Text)
    match_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4))
    source_profile_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    target_profile_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    merged_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    merged_by: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sys_user.user_id"))


class CdpIdResolutionStatus(Base):
    """Single-row throttle state for the real-time IdentityResolutionTrigger."""

    __tablename__ = "cdp_id_resolution_status"

    id: Mapped[bool] = mapped_column(Boolean, primary_key=True, server_default=text("true"))
    last_executed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))


class CdpCustomerPersona(Base):
    """Versioned, explainable "customer persona" computed from a resolved
    cdp_master_profiles row by backend-system/identity_resolution's
    PersonaResolutionEngine -- identity *understanding*, built on top of the
    identity *matching* output above. Each recomputation inserts a new row
    (computed_version increments per tenant/master_profile/persona_code);
    only the latest row per master profile has is_active = True, and that is
    the row cdp_master_profiles.current_persona_id points at."""

    __tablename__ = "cdp_customer_personas"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "master_profile_id", "persona_code", "computed_version",
            name="cdp_customer_personas_tenant_id_master_profile_id_persona_c_key",
        ),
    )

    persona_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sys_tenant.tenant_id"), nullable=False
    )
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    master_profile_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("cdp_master_profiles.master_profile_id", ondelete="CASCADE"), nullable=False
    )

    # Stable grouping key for this "family" of persona (e.g.
    # domain+value-tier+lifecycle slug) -- the (tenant_id, master_profile_id,
    # persona_code) tuple is what computed_version increments within.
    persona_code: Mapped[str] = mapped_column(Text, nullable=False)
    persona_name: Mapped[str] = mapped_column(Text, nullable=False)
    persona_category: Mapped[Optional[str]] = mapped_column(Text)
    persona_summary: Mapped[Optional[str]] = mapped_column(Text)

    persona_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 2), server_default="0")
    confidence_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4), server_default="0")
    behavior_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2), server_default="0")
    engagement_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2), server_default="0")
    financial_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2), server_default="0")
    loyalty_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2), server_default="0")
    relationship_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2), server_default="0")
    risk_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2), server_default="0")

    lifecycle_stage: Mapped[Optional[str]] = mapped_column(Text)
    customer_value_tier: Mapped[Optional[str]] = mapped_column(Text)
    risk_level: Mapped[Optional[str]] = mapped_column(Text)
    next_best_action: Mapped[Optional[str]] = mapped_column(Text)

    llm_provider: Mapped[Optional[str]] = mapped_column(Text)
    llm_model: Mapped[Optional[str]] = mapped_column(Text)
    persona_embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(768))

    computed_version: Mapped[int] = mapped_column(Integer, server_default="1")
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    computed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    expires_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))


class CdpPersonaFeature(Base):
    """One raw/derived signal (tenure, channel breadth, CLV, churn
    probability, KYC status, ...) that fed a cdp_customer_personas
    computation -- the explainability input side of the persona engine."""

    __tablename__ = "cdp_persona_features"

    feature_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    persona_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("cdp_customer_personas.persona_id", ondelete="CASCADE"), nullable=False
    )
    feature_code: Mapped[str] = mapped_column(Text, nullable=False)
    feature_name: Mapped[Optional[str]] = mapped_column(Text)
    feature_type: Mapped[Optional[str]] = mapped_column(Text)
    numeric_value: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    text_value: Mapped[Optional[str]] = mapped_column(Text)
    boolean_value: Mapped[Optional[bool]] = mapped_column(Boolean)
    source_system: Mapped[Optional[str]] = mapped_column(Text)
    confidence_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4))
    computed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))


class CdpPersonaScoreDetail(Base):
    """Per-component score breakdown (behavior/engagement/financial/loyalty/
    relationship/risk) for one cdp_customer_personas row, with the
    weight/formula/explanation behind each -- the explainability output side
    of the persona engine."""

    __tablename__ = "cdp_persona_score_details"

    score_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    persona_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("cdp_customer_personas.persona_id", ondelete="CASCADE"), nullable=False
    )
    score_type: Mapped[Optional[str]] = mapped_column(Text)
    score_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 2))
    score_weight: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    score_formula: Mapped[Optional[str]] = mapped_column(Text)
    explanation: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))


class CdpPersonaHistory(Base):
    """Audit trail of material persona changes over time (persona_name
    and/or persona_score delta above a configured threshold), one row per
    change, linked to the NEW cdp_customer_personas row that triggered it."""

    __tablename__ = "cdp_persona_history"

    history_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    persona_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("cdp_customer_personas.persona_id", ondelete="CASCADE"), nullable=False
    )
    old_persona_name: Mapped[Optional[str]] = mapped_column(Text)
    new_persona_name: Mapped[Optional[str]] = mapped_column(Text)
    old_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 2))
    new_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 2))
    change_reason: Mapped[Optional[str]] = mapped_column(Text)
    model_version: Mapped[Optional[str]] = mapped_column(Text)
    changed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))


class CdpPersonaConfig(Base):
    """Runtime config registry for PersonaResolutionEngine constants.

    Stores typed key-value config used to override in-code defaults for
    thresholds, weights, caps, and history-delta behavior.
    """

    __tablename__ = "cdp_persona_config"

    config_key: Mapped[str] = mapped_column(Text, primary_key=True)
    config_value: Mapped[str] = mapped_column(Text, nullable=False)
    data_type: Mapped[str] = mapped_column(Text, nullable=False)
    config_description: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    updated_by: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))


class CdpScoringModel(Base):
    """Registry of scoring models (Lead, Churn, CLV, CX, Data Quality, persona risk/loyalty)."""

    __tablename__ = "cdp_scoring_models"

    scoring_model_name: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    model_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ACTIVE'"))
    schedule_definition: Mapped[Optional[str]] = mapped_column(Text)
    input_features: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text), server_default=text("ARRAY[]::text[]"))
    hyperparameters: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    updated_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()")) 