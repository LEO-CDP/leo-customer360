"""Pydantic schemas for the Customer Identity Resolution (CIR) core models:
master profiles, raw profile staging, profile links, and the matching-rule
metadata / throttle-status tables consumed by backend-system/identity_resolution.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class MasterProfileBase(BaseModel):
    tenant_id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    domain: str = Field(default="retail")

    full_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    is_hashed: Optional[bool] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    secondary_emails: Optional[list[dict]] = None
    secondary_phones: Optional[list[dict]] = None
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None
    address: Optional[dict] = None
    company_name: Optional[str] = None

    external_ids: Optional[dict] = None
    device_ids: Optional[list[str]] = None
    advertising_ids: Optional[list[str]] = None
    cookie_ids: Optional[list[str]] = None
    push_tokens: Optional[dict] = None

    acquisition_source: Optional[str] = None
    acquisition_campaign: Optional[str] = None
    current_persona_id: Optional[uuid.UUID] = None
    persona_name: Optional[str] = None
    segmentation_tags: Optional[list[str]] = None
    communication_preferences: Optional[dict] = None
    attributes: Optional[dict] = None
    source_systems: Optional[list[str]] = None
    first_seen_raw_profile_id: Optional[uuid.UUID] = None

    # Customer lifecycle & engagement tracking (prospect -> lead -> customer).
    customer_since: Optional[date] = None
    last_activity_at: Optional[datetime] = None
    preferred_channel: Optional[str] = None
    lifecycle_stage: Optional[str] = Field(
        default=None, pattern="^(prospect|lead|customer|vip|dormant|churn_risk)$"
    )
    persona_summary: Optional[str] = None

    # ML & Analytics scoring models (Lead, Churn, CLV, CX, Data Quality).
    lead_conversion_probability: Optional[Decimal] = None
    lead_grade: Optional[str] = None
    churn_probability: Optional[Decimal] = None
    churn_risk_tier: Optional[str] = Field(default=None, pattern="^(low|medium|high|critical)$")
    historical_clv: Optional[Decimal] = None
    predictive_clv: Optional[Decimal] = None
    clv_segment: Optional[str] = None
    engagement_score: Optional[Decimal] = None
    latest_nps_score: Optional[int] = Field(default=None, ge=0, le=10)
    average_csat: Optional[Decimal] = None
    overall_sentiment_score: Optional[Decimal] = None
    profile_completeness_score: Optional[Decimal] = None
    identity_confidence_score: Optional[Decimal] = None
    model_versions: Optional[dict] = None
    scores_updated_at: Optional[datetime] = None


class MasterProfileCreate(MasterProfileBase):
    pass


class MasterProfileUpdate(BaseModel):
    user_id: Optional[uuid.UUID] = None
    domain: Optional[str] = Field(default=None)
    full_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    is_hashed: Optional[bool] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    secondary_emails: Optional[list[dict]] = None
    secondary_phones: Optional[list[dict]] = None
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None
    address: Optional[dict] = None
    company_name: Optional[str] = None
    external_ids: Optional[dict] = None
    device_ids: Optional[list[str]] = None
    advertising_ids: Optional[list[str]] = None
    cookie_ids: Optional[list[str]] = None
    push_tokens: Optional[dict] = None
    acquisition_source: Optional[str] = None
    acquisition_campaign: Optional[str] = None
    current_persona_id: Optional[uuid.UUID] = None
    persona_name: Optional[str] = None
    segmentation_tags: Optional[list[str]] = None
    communication_preferences: Optional[dict] = None
    attributes: Optional[dict] = None
    source_systems: Optional[list[str]] = None
    customer_since: Optional[date] = None
    last_activity_at: Optional[datetime] = None
    preferred_channel: Optional[str] = None
    lifecycle_stage: Optional[str] = Field(
        default=None, pattern="^(prospect|lead|customer|vip|dormant|churn_risk)$"
    )
    persona_summary: Optional[str] = None
    lead_conversion_probability: Optional[Decimal] = None
    lead_grade: Optional[str] = None
    churn_probability: Optional[Decimal] = None
    churn_risk_tier: Optional[str] = Field(default=None, pattern="^(low|medium|high|critical)$")
    historical_clv: Optional[Decimal] = None
    predictive_clv: Optional[Decimal] = None
    clv_segment: Optional[str] = None
    engagement_score: Optional[Decimal] = None
    latest_nps_score: Optional[int] = Field(default=None, ge=0, le=10)
    average_csat: Optional[Decimal] = None
    overall_sentiment_score: Optional[Decimal] = None
    profile_completeness_score: Optional[Decimal] = None
    identity_confidence_score: Optional[Decimal] = None
    model_versions: Optional[dict] = None
    scores_updated_at: Optional[datetime] = None
    status_code: Optional[int] = None


class MasterProfileRead(MasterProfileBase):
    model_config = ConfigDict(from_attributes=True)
    master_profile_id: uuid.UUID
    linked_raw_profile_count: int = 0
    status_code: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int
    has_prev: bool
    has_next: bool


class MasterProfileListResponse(BaseModel):
    items: list[MasterProfileRead]
    pagination: PaginationMeta


class RawProfileBase(BaseModel):
    tenant_id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    domain: str = Field(default="retail")
    source_system: str
    channel: Optional[str] = None

    external_customer_id: Optional[str] = None
    full_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    national_id: Optional[str] = None
    date_of_birth: Optional[date] = None

    # Address fields for fuzzy matching
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state_province: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = None
    company_name: Optional[str] = None

    device_id: Optional[str] = None
    advertising_id: Optional[str] = None
    platform: Optional[str] = None
    app_version: Optional[str] = None
    push_token: Optional[str] = None
    cookie_id: Optional[str] = None
    ga_client_id: Optional[str] = None
    session_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None

    media_source: Optional[str] = None
    campaign: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None

    event_name: Optional[str] = None
    event_time: Optional[datetime] = None
    event_payload: Optional[dict] = None


class RawProfileCreate(RawProfileBase):
    pass


class RawProfileUpdate(BaseModel):
    user_id: Optional[uuid.UUID] = None
    channel: Optional[str] = None
    external_customer_id: Optional[str] = None
    full_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    national_id: Optional[str] = None
    date_of_birth: Optional[date] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state_province: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = None
    company_name: Optional[str] = None
    device_id: Optional[str] = None
    advertising_id: Optional[str] = None
    platform: Optional[str] = None
    app_version: Optional[str] = None
    push_token: Optional[str] = None
    cookie_id: Optional[str] = None
    ga_client_id: Optional[str] = None
    session_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    media_source: Optional[str] = None
    campaign: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    event_name: Optional[str] = None
    event_time: Optional[datetime] = None
    event_payload: Optional[dict] = None
    status_code: Optional[int] = None


class RawProfileRead(RawProfileBase):
    model_config = ConfigDict(from_attributes=True)
    raw_profile_id: uuid.UUID
    status_code: int
    processed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class ProfileLinkBase(BaseModel):
    tenant_id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    raw_profile_id: uuid.UUID
    master_profile_id: uuid.UUID
    match_score: Optional[Decimal] = None
    match_method: Optional[str] = None
    status: str = Field(default="ACTIVE", pattern="^(ACTIVE|HISTORICAL|UNLINKED|SUPERSEDED)$")
    unlinked_at: Optional[datetime] = None
    unlinked_reason: Optional[str] = None
    unlinked_by: Optional[uuid.UUID] = None


class ProfileLinkCreate(ProfileLinkBase):
    pass


class ProfileLinkRead(ProfileLinkBase):
    model_config = ConfigDict(from_attributes=True)
    link_id: uuid.UUID
    created_at: Optional[datetime] = None


class LinkedRawProfileDetailRead(BaseModel):
    link: ProfileLinkRead
    raw_profile: RawProfileRead


class DomainProfileBase(BaseModel):
    tenant_id: uuid.UUID
    master_profile_id: uuid.UUID
    domain_id: uuid.UUID
    profile_name: Optional[str] = None
    lifecycle_stage: Optional[str] = None
    persona_name: Optional[str] = None
    persona_summary: Optional[str] = None
    engagement_score: Optional[Decimal] = None
    domain_attributes: dict[str, Any] = Field(default_factory=dict)
    analytics: Optional[dict[str, Any]] = Field(default_factory=dict)
    first_activity_at: Optional[datetime] = None
    last_activity_at: Optional[datetime] = None
    status_code: int = 1


class DomainProfileCreate(DomainProfileBase):
    pass


class DomainProfileUpdate(BaseModel):
    profile_name: Optional[str] = None
    lifecycle_stage: Optional[str] = None
    persona_name: Optional[str] = None
    persona_summary: Optional[str] = None
    engagement_score: Optional[Decimal] = None
    domain_attributes: Optional[dict[str, Any]] = None
    analytics: Optional[dict[str, Any]] = None
    first_activity_at: Optional[datetime] = None
    last_activity_at: Optional[datetime] = None
    status_code: Optional[int] = None


class DomainProfileRead(DomainProfileBase):
    model_config = ConfigDict(from_attributes=True)
    domain_profile_id: uuid.UUID
    # Resolved from sys_domain by the router (not a column on cdp_domain_profiles
    # itself) so the UI can render a domain label without a second round-trip.
    domain_code: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DomainAttributeUpsert(BaseModel):
    """Adds/overwrites a single key in a master profile's per-domain
    ``domain_attributes`` (creating the ``cdp_domain_profiles`` row for that
    domain if it doesn't exist yet). Merges into the existing JSONB rather
    than replacing it, so unrelated attributes are never lost."""

    domain: str = Field(description="Business domain owning this attribute, e.g. 'banking', 'retail'.")
    attribute_key: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    attribute_value: Any


class ProfileAttributeBase(BaseModel):
    attribute_internal_code: str
    master_profile_column: Optional[str] = None
    name: str
    description: Optional[str] = None
    attribute_group: str = "GENERAL"
    source_table: str = "cdp_master_profiles"
    status: str = "ACTIVE"
    data_type: str = "TEXT"
    # Validated against sys_domain ('all' + active domain codes) via
    # core.utils.domains.validate_domain_value in the router, not a static pattern.
    domain_scope: str = "all"
    is_pii: bool = False
    is_segmentable: bool = True

    is_identity_resolution: bool = False
    matching_rule: Optional[str] = Field(default=None, pattern="^(exact|fuzzy_trgm|fuzzy_dmetaphone|none)$")
    matching_threshold: Optional[Decimal] = None
    consolidation_rule: Optional[str] = None
    consolidation_config: dict = Field(default_factory=dict)
    priority_rank: int = 99
    value_limit: int = 5
    limit_timeframe: str = "5_annually"
    blocked_values: list = Field(default_factory=lambda: ["null", "-1", "anonymous", "void", "abc123"])
    blocked_patterns: Optional[list[str]] = Field(default_factory=lambda: ["^[0-]*$"])

    is_scoring_model: bool = False
    scoring_model_name: Optional[str] = None
    scoring_model_version: Optional[str] = None
    value_type: Optional[str] = None
    value_min: Optional[Decimal] = None
    value_max: Optional[Decimal] = None
    refresh_frequency: Optional[str] = None
    display_order: int = 0


class ProfileAttributeCreate(ProfileAttributeBase):
    pass


class ProfileAttributeUpdate(BaseModel):
    master_profile_column: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    attribute_group: Optional[str] = None
    source_table: Optional[str] = None
    status: Optional[str] = None
    data_type: Optional[str] = None
    domain_scope: Optional[str] = None
    is_pii: Optional[bool] = None
    is_segmentable: Optional[bool] = None
    is_identity_resolution: Optional[bool] = None
    matching_rule: Optional[str] = Field(default=None, pattern="^(exact|fuzzy_trgm|fuzzy_dmetaphone|none)$")
    matching_threshold: Optional[Decimal] = None
    consolidation_rule: Optional[str] = None
    consolidation_config: Optional[dict] = None
    priority_rank: Optional[int] = None
    value_limit: Optional[int] = None
    limit_timeframe: Optional[str] = None
    blocked_values: Optional[list] = None
    blocked_patterns: Optional[list[str]] = None
    is_scoring_model: Optional[bool] = None
    scoring_model_name: Optional[str] = None
    scoring_model_version: Optional[str] = None
    value_type: Optional[str] = None
    value_min: Optional[Decimal] = None
    value_max: Optional[Decimal] = None
    refresh_frequency: Optional[str] = None
    display_order: Optional[int] = None


class ProfileAttributeRead(ProfileAttributeBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class IdResolutionStatusRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: bool
    last_executed_at: Optional[datetime] = None


class IdentityIndexBase(BaseModel):
    tenant_id: uuid.UUID
    master_profile_id: uuid.UUID
    identifier_type: str
    identifier_value: str
    identifier_value_normalized: str
    is_primary: bool = False
    is_blocked: bool = False


class IdentityIndexCreate(IdentityIndexBase):
    pass


class IdentityIndexUpdate(BaseModel):
    is_primary: Optional[bool] = None
    is_blocked: Optional[bool] = None
    last_seen_at: Optional[datetime] = None


class IdentityIndexRead(IdentityIndexBase):
    model_config = ConfigDict(from_attributes=True)
    identity_index_id: uuid.UUID
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None


class ProfileMergeHistoryBase(BaseModel):
    tenant_id: uuid.UUID
    target_master_profile_id: uuid.UUID
    source_master_profile_id: uuid.UUID
    merge_reason: str
    matched_identifier_type: Optional[str] = None
    matched_identifier_value: Optional[str] = None
    match_score: Optional[Decimal] = None
    source_profile_snapshot: dict
    target_profile_snapshot: dict
    merged_by: Optional[uuid.UUID] = None


class ProfileMergeHistoryCreate(ProfileMergeHistoryBase):
    pass


class ProfileMergeHistoryRead(ProfileMergeHistoryBase):
    model_config = ConfigDict(from_attributes=True)
    merge_id: uuid.UUID
    merged_at: Optional[datetime] = None


# --- Customer Persona Resolution ("identity understanding") -------------------
# A genuine many-to-many relationship: one shared CdpPersonaArchetype can be
# matched by many master profiles (via CustomerPersonaRead rows), and one
# master profile accumulates many versioned CustomerPersonaRead rows over time.


class PersonaArchetypeBase(BaseModel):
    tenant_id: uuid.UUID
    domain: str = Field(default="retail")

    persona_code: str
    persona_name: str
    persona_category: Optional[str] = None
    persona_summary: Optional[str] = None

    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None

    centroid_behavior_score: Optional[Decimal] = None
    centroid_engagement_score: Optional[Decimal] = None
    centroid_financial_score: Optional[Decimal] = None
    centroid_loyalty_score: Optional[Decimal] = None
    centroid_relationship_score: Optional[Decimal] = None
    centroid_risk_score: Optional[Decimal] = None

    is_active: bool = True


class PersonaArchetypeCreate(PersonaArchetypeBase):
    pass


class PersonaArchetypeUpdate(BaseModel):
    persona_name: Optional[str] = None
    persona_category: Optional[str] = None
    persona_summary: Optional[str] = None
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    is_active: Optional[bool] = None


class PersonaArchetypeRead(PersonaArchetypeBase):
    model_config = ConfigDict(from_attributes=True)
    persona_archetype_id: uuid.UUID
    # Denormalized COUNT(DISTINCT master_profile_id) across ACTIVE matches --
    # the "Total Matched Profiles" figure the Persona Management admin UI
    # must display per archetype (maintained by a DB trigger, read-only here).
    matched_profile_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CustomerPersonaBase(BaseModel):
    tenant_id: uuid.UUID
    domain: str = Field(default="retail")
    master_profile_id: uuid.UUID
    # The shared archetype this profile is matched/assigned to.
    persona_archetype_id: uuid.UUID

    # Lookalike match quality vs the archetype's centroid.
    match_score: Optional[Decimal] = None

    persona_score: Optional[Decimal] = None
    confidence_score: Optional[Decimal] = None
    behavior_score: Optional[Decimal] = None
    engagement_score: Optional[Decimal] = None
    financial_score: Optional[Decimal] = None
    loyalty_score: Optional[Decimal] = None
    relationship_score: Optional[Decimal] = None
    risk_score: Optional[Decimal] = None

    lifecycle_stage: Optional[str] = Field(
        default=None, pattern="^(prospect|lead|customer|vip|dormant|churn_risk)$"
    )
    customer_value_tier: Optional[str] = None
    risk_level: Optional[str] = Field(default=None, pattern="^(low|medium|high|critical)$")
    next_best_action: Optional[str] = None

    computed_version: int = 1
    is_active: bool = True
    expires_at: Optional[datetime] = None


class CustomerPersonaCreate(CustomerPersonaBase):
    pass


class CustomerPersonaUpdate(BaseModel):
    match_score: Optional[Decimal] = None
    persona_score: Optional[Decimal] = None
    confidence_score: Optional[Decimal] = None
    behavior_score: Optional[Decimal] = None
    engagement_score: Optional[Decimal] = None
    financial_score: Optional[Decimal] = None
    loyalty_score: Optional[Decimal] = None
    relationship_score: Optional[Decimal] = None
    risk_score: Optional[Decimal] = None
    lifecycle_stage: Optional[str] = Field(
        default=None, pattern="^(prospect|lead|customer|vip|dormant|churn_risk)$"
    )
    customer_value_tier: Optional[str] = None
    risk_level: Optional[str] = Field(default=None, pattern="^(low|medium|high|critical)$")
    next_best_action: Optional[str] = None
    is_active: Optional[bool] = None
    expires_at: Optional[datetime] = None


class CustomerPersonaRead(CustomerPersonaBase):
    model_config = ConfigDict(from_attributes=True)
    persona_id: uuid.UUID
    computed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PersonaAnalyticsBucket(BaseModel):
    value: str
    count: int


class PersonaAnalyticsSummary(BaseModel):
    total_archetypes: int
    total_personas: int
    active_personas: int
    inactive_personas: int
    unique_master_profiles: int
    avg_persona_score: float
    avg_confidence_score: float
    by_domain: list[PersonaAnalyticsBucket]
    by_category: list[PersonaAnalyticsBucket]
    by_risk_level: list[PersonaAnalyticsBucket]
    by_value_tier: list[PersonaAnalyticsBucket]


class PersonaFeatureBase(BaseModel):
    persona_id: uuid.UUID
    feature_code: str
    feature_name: Optional[str] = None
    feature_type: Optional[str] = Field(default=None, pattern="^(numeric|text|boolean)$")
    numeric_value: Optional[Decimal] = None
    text_value: Optional[str] = None
    boolean_value: Optional[bool] = None
    source_system: Optional[str] = None
    confidence_score: Optional[Decimal] = None


class PersonaFeatureCreate(PersonaFeatureBase):
    pass


class PersonaFeatureRead(PersonaFeatureBase):
    model_config = ConfigDict(from_attributes=True)
    feature_id: uuid.UUID
    computed_at: Optional[datetime] = None


class PersonaScoreDetailBase(BaseModel):
    persona_id: uuid.UUID
    score_type: str
    score_value: Optional[Decimal] = None
    score_weight: Optional[Decimal] = None
    score_formula: Optional[str] = None
    explanation: Optional[str] = None


class PersonaScoreDetailCreate(PersonaScoreDetailBase):
    pass


class PersonaScoreDetailRead(PersonaScoreDetailBase):
    model_config = ConfigDict(from_attributes=True)
    score_id: uuid.UUID
    created_at: Optional[datetime] = None


class PersonaHistoryBase(BaseModel):
    persona_id: uuid.UUID
    old_persona_name: Optional[str] = None
    new_persona_name: Optional[str] = None
    old_score: Optional[Decimal] = None
    new_score: Optional[Decimal] = None
    change_reason: Optional[str] = None
    model_version: Optional[str] = None


class PersonaHistoryCreate(PersonaHistoryBase):
    pass


class PersonaHistoryRead(PersonaHistoryBase):
    model_config = ConfigDict(from_attributes=True)
    history_id: uuid.UUID
    changed_at: Optional[datetime] = None
