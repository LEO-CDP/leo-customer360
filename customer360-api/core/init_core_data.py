

"""Startup-time seed/init data for the Customer 360 API.

Currently seeds a small set of default segmentation tags (``cdp_segments``)
for every tenant that doesn't have any yet, so a fresh install already has a
usable Audience Builder starting point instead of an empty segment list.

Called once from ``app.py``'s startup eve-nt. Safe to call on every app
startup: it's idempotent (skips tenants that already have >= 1 segment, and
the ``(tenant_id, segment_tag)`` unique constraint on ``cdp_segments`` is a
second safety net against duplicate inserts under concurrent startups).
"""

import logging
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.config import settings
from core.database import SessionLocal
from core.models.segmentation import CdpSegment
from core.models.system import SysUser, SysUserInfo
from core.repositories.metadata_repository import DEFAULT_TENANT_ID
from core.utils.security import hash_password

logger = logging.getLogger(__name__)

# System-default segments seeded for every new tenant. json_rules mirrors the
# jQuery QueryBuilder rule tree an admin would build in the UI; sql_rules is
# the equivalent translated WHERE-clause fragment against cdp_master_profiles.
#
# Organized by domain so a fresh environment has useful out-of-the-box audience
# templates for retail/ecommerce, travel, education, and real estate use cases.
COMMON_SEGMENTS: list[dict[str, Any]] = [
    {
        "segment_tag": "new_customer",
        "segment_name": "New Customers",
        "description": "Profiles that became a paying customer in the last 30 days.",
        "json_rules": {
            "condition": "AND",
            "rules": [{"field": "customer_since", "operator": "greater_or_equal", "value": "-30 days"}],
        },
        "sql_rules": "customer_since >= (CURRENT_DATE - INTERVAL '30 days')",
    },
    {
        "segment_tag": "high_value",
        "segment_name": "High-Value Customers",
        "description": "Profiles with predictive customer lifetime value above 1000.",
        "json_rules": {
            "condition": "AND",
            "rules": [{"field": "predictive_clv", "operator": "greater", "value": 1000}],
        },
        "sql_rules": "predictive_clv > 1000",
    },
    {
        "segment_tag": "churn_risk",
        "segment_name": "At Risk of Churn",
        "description": "Profiles with a high or critical churn risk tier.",
        "json_rules": {
            "condition": "AND",
            "rules": [{"field": "churn_risk_tier", "operator": "in", "value": ["high", "critical"]}],
        },
        "sql_rules": "churn_risk_tier IN ('high', 'critical')",
    },
    {
        "segment_tag": "dormant",
        "segment_name": "Dormant Profiles",
        "description": "Profiles with no activity in the last 90 days.",
        "json_rules": {
            "condition": "AND",
            "rules": [{"field": "last_activity_at", "operator": "less", "value": "-90 days"}],
        },
        "sql_rules": "last_activity_at < (now() - INTERVAL '90 days')",
    },
    {
        "segment_tag": "recently_active",
        "segment_name": "Recently Active",
        "description": "Profiles active in the last 30 days.",
        "json_rules": {
            "condition": "AND",
            "rules": [{"field": "last_activity_at", "operator": "greater_or_equal", "value": "-30 days"}],
        },
        "sql_rules": "last_activity_at >= (now() - INTERVAL '30 days')",
    },
    {
        "segment_tag": "growth_potential",
        "segment_name": "Growth Potential",
        "description": "Mid-value profiles with room to grow into high-value customers.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "predictive_clv", "operator": "greater_or_equal", "value": 500},
                {"field": "predictive_clv", "operator": "less", "value": 1001},
            ],
        },
        "sql_rules": "predictive_clv >= 500 AND predictive_clv < 1001",
    },
    {
        "segment_tag": "win_back",
        "segment_name": "Win-Back Candidates",
        "description": "Profiles inactive for 30-180 days with elevated churn risk.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "last_activity_at", "operator": "less", "value": "-30 days"},
                {"field": "last_activity_at", "operator": "greater", "value": "-180 days"},
                {"field": "churn_risk_tier", "operator": "in", "value": ["medium", "high", "critical"]},
            ],
        },
        "sql_rules": (
            "last_activity_at < (now() - INTERVAL '30 days') "
            "AND last_activity_at > (now() - INTERVAL '180 days') "
            "AND churn_risk_tier IN ('medium', 'high', 'critical')"
        ),
    },
    {
        "segment_tag": "champions",
        "segment_name": "Champions",
        "description": "Long-tenure, top-value customers to prioritize for loyalty experiences.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "predictive_clv", "operator": "greater", "value": 2500},
                {"field": "customer_since", "operator": "less", "value": "-365 days"},
            ],
        },
        "sql_rules": "predictive_clv > 2500 AND customer_since < (CURRENT_DATE - INTERVAL '365 days')",
    },
    {
        "segment_tag": "high_engagement",
        "segment_name": "High Engagement",
        "description": "Profiles with consistently high engagement scores.",
        "json_rules": {
            "condition": "AND",
            "rules": [{"field": "engagement_score", "operator": "greater_or_equal", "value": 75}],
        },
        "sql_rules": "engagement_score >= 75",
    },
    {
        "segment_tag": "low_engagement",
        "segment_name": "Low Engagement",
        "description": "Profiles that may need re-engagement due to low interaction.",
        "json_rules": {
            "condition": "AND",
            "rules": [{"field": "engagement_score", "operator": "less", "value": 30}],
        },
        "sql_rules": "engagement_score < 30",
    },
    {
        "segment_tag": "promoters",
        "segment_name": "Promoters",
        "description": "Profiles with strong NPS advocacy signals.",
        "json_rules": {
            "condition": "AND",
            "rules": [{"field": "latest_nps_score", "operator": "greater_or_equal", "value": 9}],
        },
        "sql_rules": "latest_nps_score >= 9",
    },
    {
        "segment_tag": "detractors",
        "segment_name": "Detractors",
        "description": "Profiles with low NPS scores that may need service recovery.",
        "json_rules": {
            "condition": "AND",
            "rules": [{"field": "latest_nps_score", "operator": "less", "value": 7}],
        },
        "sql_rules": "latest_nps_score < 7",
    },
    {
        "segment_tag": "hot_leads",
        "segment_name": "Hot Leads",
        "description": "Prospects with high conversion probability.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "lifecycle_stage", "operator": "in", "value": ["prospect", "lead"]},
                {"field": "lead_conversion_probability", "operator": "greater_or_equal", "value": 0.7},
            ],
        },
        "sql_rules": "lifecycle_stage IN ('prospect', 'lead') AND lead_conversion_probability >= 0.7",
    },
    {
        "segment_tag": "identity_review_needed",
        "segment_name": "Needs Identity Review",
        "description": "Profiles with low identity confidence scores.",
        "json_rules": {
            "condition": "AND",
            "rules": [{"field": "identity_confidence_score", "operator": "less", "value": 0.6}],
        },
        "sql_rules": "identity_confidence_score < 0.6",
    },
    {
        "segment_tag": "profile_enrichment_needed",
        "segment_name": "Needs Profile Enrichment",
        "description": "Profiles missing important data fields.",
        "json_rules": {
            "condition": "AND",
            "rules": [{"field": "profile_completeness_score", "operator": "less", "value": 70}],
        },
        "sql_rules": "profile_completeness_score < 70",
    },
]

RETAIL_SEGMENTS: list[dict[str, Any]] = [
    {
        "segment_tag": "retail_store_loyalists",
        "segment_name": "Store-First Shoppers",
        "description": "Retail customers who mostly shop in physical stores.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "domain", "operator": "in", "value": ["retail"]},
                {"field": "preferred_channel", "operator": "in", "value": ["In-Store", "POS", "Store"]},
            ],
        },
        "sql_rules": "domain = 'retail' AND preferred_channel IN ('In-Store', 'POS', 'Store')",
    },
    {
        "segment_tag": "retail_discount_seekers",
        "segment_name": "Retail Discount Seekers",
        "description": "Retail customers acquired through discount-heavy channels.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "domain", "operator": "in", "value": ["retail"]},
                {
                    "field": "acquisition_source",
                    "operator": "in",
                    "value": ["coupon", "affiliate", "paid_social", "deal_site"],
                },
            ],
        },
        "sql_rules": "domain = 'retail' AND acquisition_source IN ('coupon', 'affiliate', 'paid_social', 'deal_site')",
    },
    {
        "segment_tag": "retail_high_basket_value",
        "segment_name": "High-Spend Retail Customers",
        "description": "Retail customers with high predicted lifetime value.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "domain", "operator": "in", "value": ["retail"]},
                {"field": "predictive_clv", "operator": "greater_or_equal", "value": 1500},
            ],
        },
        "sql_rules": "domain = 'retail' AND predictive_clv >= 1500",
    },
    {
        "segment_tag": "retail_at_risk_regulars",
        "segment_name": "At-Risk Retail Regulars",
        "description": "Frequent retail customers who may stop buying soon.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "domain", "operator": "in", "value": ["retail"]},
                {"field": "churn_risk_tier", "operator": "in", "value": ["high", "critical"]},
                {"field": "last_activity_at", "operator": "less", "value": "-14 days"},
            ],
        },
        "sql_rules": "domain = 'retail' AND churn_risk_tier IN ('high', 'critical') AND last_activity_at < (now() - INTERVAL '14 days')",
    },
    {
        "segment_tag": "retail_omnichannel_shoppers",
        "segment_name": "Retail Omnichannel Shoppers",
        "description": "Retail customers active across multiple identity touchpoints.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "domain", "operator": "in", "value": ["retail"]},
                {"field": "linked_raw_profile_count", "operator": "greater_or_equal", "value": 3},
                {"field": "engagement_score", "operator": "greater_or_equal", "value": 60},
            ],
        },
        "sql_rules": "domain = 'retail' AND linked_raw_profile_count >= 3 AND engagement_score >= 60",
    },
]

ECOMMERCE_SEGMENTS: list[dict[str, Any]] = [
    {
        "segment_tag": "ecommerce_new_visitors",
        "segment_name": "New Online Customers",
        "description": "Online-first customers who converted in the last 30 days.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "domain", "operator": "in", "value": ["retail"]},
                {"field": "preferred_channel", "operator": "in", "value": ["Website", "Mobile App"]},
                {"field": "customer_since", "operator": "greater_or_equal", "value": "-30 days"},
            ],
        },
        "sql_rules": (
            "domain = 'retail' AND preferred_channel IN ('Website', 'Mobile App') "
            "AND customer_since >= (CURRENT_DATE - INTERVAL '30 days')"
        ),
    },
    {
        "segment_tag": "ecommerce_power_buyers",
        "segment_name": "Top Online Customers",
        "description": "Online-first customers with very high predicted value.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "domain", "operator": "in", "value": ["retail"]},
                {"field": "preferred_channel", "operator": "in", "value": ["Website", "Mobile App"]},
                {"field": "predictive_clv", "operator": "greater", "value": 2000},
            ],
        },
        "sql_rules": "domain = 'retail' AND preferred_channel IN ('Website', 'Mobile App') AND predictive_clv > 2000",
    },
    {
        "segment_tag": "ecommerce_conversion_nudge",
        "segment_name": "Likely Online Buyers",
        "description": "Prospects likely to buy soon through web or app channels.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "domain", "operator": "in", "value": ["retail"]},
                {"field": "preferred_channel", "operator": "in", "value": ["Website", "Mobile App"]},
                {"field": "lead_conversion_probability", "operator": "greater_or_equal", "value": 0.45},
                {"field": "lifecycle_stage", "operator": "in", "value": ["prospect", "lead"]},
            ],
        },
        "sql_rules": (
            "domain = 'retail' AND preferred_channel IN ('Website', 'Mobile App') "
            "AND lead_conversion_probability >= 0.45 AND lifecycle_stage IN ('prospect', 'lead')"
        ),
    },
    {
        "segment_tag": "ecommerce_churn_watch",
        "segment_name": "Online Churn Watchlist",
        "description": "Online customers showing early churn risk signs.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "domain", "operator": "in", "value": ["retail"]},
                {"field": "preferred_channel", "operator": "in", "value": ["Website", "Mobile App"]},
                {"field": "churn_risk_tier", "operator": "in", "value": ["medium", "high", "critical"]},
            ],
        },
        "sql_rules": "domain = 'retail' AND preferred_channel IN ('Website', 'Mobile App') AND churn_risk_tier IN ('medium', 'high', 'critical')",
    },
    {
        "segment_tag": "ecommerce_reactivation_90d",
        "segment_name": "Inactive Online Customers (90+ Days)",
        "description": "Online customers with no activity for at least 90 days.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "domain", "operator": "in", "value": ["retail"]},
                {"field": "preferred_channel", "operator": "in", "value": ["Website", "Mobile App"]},
                {"field": "last_activity_at", "operator": "less", "value": "-90 days"},
            ],
        },
        "sql_rules": (
            "domain = 'retail' AND preferred_channel IN ('Website', 'Mobile App') "
            "AND last_activity_at < (now() - INTERVAL '90 days')"
        ),
    },
]

TRAVEL_SEGMENTS: list[dict[str, Any]] = [
    {
        "segment_tag": "travel_frequent_travelers",
        "segment_name": "Frequent Travelers",
        "description": "Travel customers with frequent and high engagement activity.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "domain", "operator": "in", "value": ["travel"]},
                {"field": "engagement_score", "operator": "greater_or_equal", "value": 70},
                {"field": "linked_raw_profile_count", "operator": "greater_or_equal", "value": 3},
            ],
        },
        "sql_rules": "domain = 'travel' AND engagement_score >= 70 AND linked_raw_profile_count >= 3",
    },
    {
        "segment_tag": "travel_premium_travelers",
        "segment_name": "Premium Travelers",
        "description": "Travel customers with high lifetime value potential.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "domain", "operator": "in", "value": ["travel"]},
                {"field": "predictive_clv", "operator": "greater", "value": 2500},
            ],
        },
        "sql_rules": "domain = 'travel' AND predictive_clv > 2500",
    },
    {
        "segment_tag": "travel_booking_dropout_risk",
        "segment_name": "Travel Booking Dropout Risk",
        "description": "Travel leads with high intent but signs of drop-off.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "domain", "operator": "in", "value": ["travel"]},
                {"field": "lifecycle_stage", "operator": "in", "value": ["prospect", "lead"]},
                {"field": "lead_conversion_probability", "operator": "greater_or_equal", "value": 0.5},
                {"field": "last_activity_at", "operator": "less", "value": "-14 days"},
            ],
        },
        "sql_rules": (
            "domain = 'travel' AND lifecycle_stage IN ('prospect', 'lead') "
            "AND lead_conversion_probability >= 0.5 AND last_activity_at < (now() - INTERVAL '14 days')"
        ),
    },
    {
        "segment_tag": "travel_loyalty_growth",
        "segment_name": "Growing Travel Loyalists",
        "description": "Established travel customers with medium-to-high value potential.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "domain", "operator": "in", "value": ["travel"]},
                {"field": "customer_since", "operator": "less", "value": "-365 days"},
                {"field": "predictive_clv", "operator": "greater_or_equal", "value": 800},
                {"field": "predictive_clv", "operator": "less", "value": 2500},
            ],
        },
        "sql_rules": (
            "domain = 'travel' AND customer_since < (CURRENT_DATE - INTERVAL '365 days') "
            "AND predictive_clv >= 800 AND predictive_clv < 2500"
        ),
    },
    {
        "segment_tag": "travel_churn_alert",
        "segment_name": "Travel Churn Alert",
        "description": "Travel customers with high immediate churn risk.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "domain", "operator": "in", "value": ["travel"]},
                {"field": "churn_risk_tier", "operator": "in", "value": ["high", "critical"]},
            ],
        },
        "sql_rules": "domain = 'travel' AND churn_risk_tier IN ('high', 'critical')",
    },
]

EDUCATION_SEGMENTS: list[dict[str, Any]] = [
    {
        "segment_tag": "education_high_intent_leads",
        "segment_name": "High-Intent Education Leads",
        "description": "Education-domain prospects with strong enrollment intent.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "domain", "operator": "in", "value": ["education"]},
                {"field": "lifecycle_stage", "operator": "in", "value": ["prospect", "lead"]},
                {"field": "lead_conversion_probability", "operator": "greater_or_equal", "value": 0.65},
            ],
        },
        "sql_rules": (
            "domain = 'education' AND lifecycle_stage IN ('prospect', 'lead') "
            "AND lead_conversion_probability >= 0.65"
        ),
    },
    {
        "segment_tag": "education_active_learners",
        "segment_name": "Education Active Learners",
        "description": "Recently active learners with high engagement.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "domain", "operator": "in", "value": ["education"]},
                {"field": "engagement_score", "operator": "greater_or_equal", "value": 70},
                {"field": "last_activity_at", "operator": "greater_or_equal", "value": "-14 days"},
            ],
        },
        "sql_rules": "domain = 'education' AND engagement_score >= 70 AND last_activity_at >= (now() - INTERVAL '14 days')",
    },
    {
        "segment_tag": "education_completion_risk",
        "segment_name": "Learners at Dropout Risk",
        "description": "Learners with low engagement and high dropout risk signals.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "domain", "operator": "in", "value": ["education"]},
                {"field": "engagement_score", "operator": "less", "value": 40},
                {"field": "churn_risk_tier", "operator": "in", "value": ["medium", "high", "critical"]},
            ],
        },
        "sql_rules": "domain = 'education' AND engagement_score < 40 AND churn_risk_tier IN ('medium', 'high', 'critical')",
    },
    {
        "segment_tag": "education_alumni_advocates",
        "segment_name": "Alumni Advocates",
        "description": "Long-tenure education customers with strong advocacy signals.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "domain", "operator": "in", "value": ["education"]},
                {"field": "customer_since", "operator": "less", "value": "-365 days"},
                {"field": "latest_nps_score", "operator": "greater_or_equal", "value": 9},
            ],
        },
        "sql_rules": "domain = 'education' AND customer_since < (CURRENT_DATE - INTERVAL '365 days') AND latest_nps_score >= 9",
    },
    {
        "segment_tag": "education_reenrollment_candidates",
        "segment_name": "Re-enrollment Candidates",
        "description": "Previously engaged learners likely to return.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "domain", "operator": "in", "value": ["education"]},
                {"field": "last_activity_at", "operator": "less", "value": "-120 days"},
                {"field": "lead_conversion_probability", "operator": "greater_or_equal", "value": 0.4},
            ],
        },
        "sql_rules": "domain = 'education' AND last_activity_at < (now() - INTERVAL '120 days') AND lead_conversion_probability >= 0.4",
    },
]

REAL_ESTATE_SEGMENTS: list[dict[str, Any]] = [
    {
        "segment_tag": "real_estate_hot_buyers",
        "segment_name": "Real Estate Hot Buyers",
        "description": "High-intent real estate leads prioritized for fast follow-up.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "domain", "operator": "in", "value": ["real_estate"]},
                {"field": "lead_grade", "operator": "in", "value": ["A", "Hot"]},
            ],
        },
        "sql_rules": "domain = 'real_estate' AND lead_grade IN ('A', 'Hot')",
    },
    {
        "segment_tag": "real_estate_investor_profile",
        "segment_name": "Investor-Like Property Buyers",
        "description": "Real estate profiles with high investment value potential.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "domain", "operator": "in", "value": ["real_estate"]},
                {"field": "predictive_clv", "operator": "greater", "value": 3000},
            ],
        },
        "sql_rules": "domain = 'real_estate' AND predictive_clv > 3000",
    },
    {
        "segment_tag": "real_estate_nurture_long_cycle",
        "segment_name": "Long-Cycle Property Leads",
        "description": "Early-stage real estate leads in a long consideration journey.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "domain", "operator": "in", "value": ["real_estate"]},
                {"field": "lifecycle_stage", "operator": "in", "value": ["prospect", "lead"]},
                {"field": "lead_conversion_probability", "operator": "greater_or_equal", "value": 0.35},
                {"field": "last_activity_at", "operator": "less", "value": "-30 days"},
            ],
        },
        "sql_rules": (
            "domain = 'real_estate' AND lifecycle_stage IN ('prospect', 'lead') "
            "AND lead_conversion_probability >= 0.35 AND last_activity_at < (now() - INTERVAL '30 days')"
        ),
    },
    {
        "segment_tag": "real_estate_tour_ready",
        "segment_name": "Tour-Ready Property Leads",
        "description": "Real estate leads showing strong readiness for a property tour.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "domain", "operator": "in", "value": ["real_estate"]},
                {"field": "engagement_score", "operator": "greater_or_equal", "value": 65},
                {"field": "lead_conversion_probability", "operator": "greater_or_equal", "value": 0.55},
            ],
        },
        "sql_rules": "domain = 'real_estate' AND engagement_score >= 65 AND lead_conversion_probability >= 0.55",
    },
    {
        "segment_tag": "real_estate_mortgage_risk",
        "segment_name": "At-Risk Property Buyers",
        "description": "Real estate profiles with risk signals and negative sentiment.",
        "json_rules": {
            "condition": "AND",
            "rules": [
                {"field": "domain", "operator": "in", "value": ["real_estate"]},
                {"field": "churn_risk_tier", "operator": "in", "value": ["medium", "high", "critical"]},
                {"field": "overall_sentiment_score", "operator": "less", "value": 0},
            ],
        },
        "sql_rules": "domain = 'real_estate' AND churn_risk_tier IN ('medium', 'high', 'critical') AND overall_sentiment_score < 0",
    },
]

def _with_domain(segments: Sequence[dict[str, Any]], domain: str) -> list[dict[str, Any]]:
    """Returns a copy of each segment with explicit cdp_segments.domain."""
    return [{**seg, "domain": domain} for seg in segments]


DEFAULT_SEGMENTS: list[dict[str, Any]] = [
    *_with_domain(COMMON_SEGMENTS, "all"),
    *_with_domain(RETAIL_SEGMENTS, "retail"),
    *_with_domain(ECOMMERCE_SEGMENTS, "retail"),
    *_with_domain(TRAVEL_SEGMENTS, "travel"),
    *_with_domain(EDUCATION_SEGMENTS, "education"),
    *_with_domain(REAL_ESTATE_SEGMENTS, "real_estate"),
]


def _final_generated_sql(sql_rules: str) -> str:
    return (
        f"SELECT master_profile_id FROM {settings.db_schema}.cdp_master_profiles "
        f"WHERE tenant_id = :tenant_id AND ({sql_rules})"
    )


def list_tenant_ids(db: Session) -> list[uuid.UUID]:
    """Returns all tenant IDs currently present in ``sys_tenant``."""
    return [row[0] for row in db.execute(text(f"SELECT tenant_id FROM {settings.db_schema}.sys_tenant")).all()]


def seed_default_segments_with_breakdown(
    db: Session,
    *,
    tenant_ids: Sequence[uuid.UUID] | None = None,
) -> tuple[int, dict[uuid.UUID, int]]:
    """Backfills missing ``DEFAULT_SEGMENTS`` for each target tenant.

    Unlike a one-time bootstrap, this function is safe for repeated runs in a
    growing SaaS system: if new defaults are introduced later, existing tenants
    receive only the missing tags while custom tenant-defined segments remain
    untouched.
    """
    target_tenant_ids = list(tenant_ids) if tenant_ids is not None else list_tenant_ids(db)

    inserted = 0
    inserted_by_tenant: dict[uuid.UUID, int] = {}
    for tenant_id in target_tenant_ids:
        # Scope this connection to the tenant being seeded before touching
        # any tenant-scoped/RLS-protected table -- same pattern as
        # backend-system/identity_resolution's per-row set_config (see resolver.py).
        db.execute(text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": str(tenant_id)})

        existing_tags = {
            row[0]
            for row in db.execute(select(CdpSegment.segment_tag).where(CdpSegment.tenant_id == tenant_id)).all()
        }
        missing_segments = [seg for seg in DEFAULT_SEGMENTS if seg["segment_tag"] not in existing_tags]
        if not missing_segments:
            continue

        rows_to_insert = [
            {
                "tenant_id": tenant_id,
                "domain": seg["domain"],
                "segment_tag": seg["segment_tag"],
                "segment_name": seg["segment_name"],
                "description": seg["description"],
                "json_rules": seg["json_rules"],
                "sql_rules": seg["sql_rules"],
                "final_generated_sql": _final_generated_sql(seg["sql_rules"]),
                "processed_by": "human",
            }
            for seg in missing_segments
        ]

        try:
            result = db.execute(
                pg_insert(CdpSegment)
                .values(rows_to_insert)
                .on_conflict_do_nothing(index_elements=[CdpSegment.tenant_id, CdpSegment.segment_tag])
            )
            db.commit()
            rowcount = getattr(result, "rowcount", None)
            inserted_now = int(rowcount if rowcount is not None else len(rows_to_insert))
            inserted += inserted_now
            inserted_by_tenant[tenant_id] = inserted_now
        except IntegrityError:
            # Another worker/process seeded this tenant concurrently -- safe to skip.
            db.rollback()
            logger.info("Default segments already seeded for tenant %s (concurrent init), skipping.", tenant_id)

    return inserted, inserted_by_tenant


def seed_default_segments(db: Session, *, tenant_ids: Sequence[uuid.UUID] | None = None) -> int:
    """Ensures target tenants have all ``DEFAULT_SEGMENTS``.

    Returns the total number of new segment rows inserted.
    """
    inserted, _ = seed_default_segments_with_breakdown(db, tenant_ids=tenant_ids)
    return inserted


def seed_root_admin_user(db: Session, *, tenant_id: uuid.UUID = DEFAULT_TENANT_ID) -> bool:
    """Ensures DEFAULT_ROOT_USERNAME has a real ``sys_user`` (+ LOCAL
    ``sys_userinfo``) row in ``tenant_id``.

    POST /auth/login (dev mode, SSO_LOGIN=false) authenticates this account
    against DEFAULT_ROOT_USERNAME/PASSWORD, but every other endpoint
    (get_current_user, etc.) requires an actual sys_user row to resolve
    ``request.state.user_id`` against -- without one, the root login worked
    but every subsequent API call 401'd. The password itself lives on
    ``sys_userinfo`` (auth_provider='LOCAL'), matching every other local
    credential -- ``sys_user`` has no password column (see
    database-schema.sql). Idempotent: safe to run on every startup, and keeps
    the hash in sync if DEFAULT_ROOT_PASSWORD changes in .env.
    """
    if not settings.default_root_password:
        return False

    db.execute(text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": str(tenant_id)})
    username = settings.default_root_username.strip().lower()

    try:
        inserted_user = db.execute(
            pg_insert(SysUser)
            .values(
                tenant_id=tenant_id,
                username=username,
                full_name="Root Administrator",
                status="ACTIVE",
            )
            .on_conflict_do_nothing(index_elements=[SysUser.tenant_id, SysUser.username])
            .returning(SysUser.user_id)
        ).first()

        user_id = inserted_user[0] if inserted_user else db.execute(
            select(SysUser.user_id).where(SysUser.tenant_id == tenant_id, SysUser.username == username)
        ).scalar_one()

        db.execute(
            pg_insert(SysUserInfo)
            .values(
                tenant_id=tenant_id,
                user_id=user_id,
                auth_provider="LOCAL",
                provider_subject_id=username,
                password_hash=hash_password(settings.default_root_password),
                status="ACTIVE",
            )
            .on_conflict_do_update(
                index_elements=[SysUserInfo.tenant_id, SysUserInfo.auth_provider, SysUserInfo.provider_subject_id],
                set_={"password_hash": hash_password(settings.default_root_password), "updated_at": text("now()")},
            )
        )
        db.commit()
        return inserted_user is not None
    except IntegrityError:
        db.rollback()
        return False


def init_core_data() -> None:
    """Runs all startup-time seed/init steps for the API.

    Called during the application startup event so all necessary data is in
    place before the app starts serving requests. Failures are logged and
    swallowed rather than raised, so a seeding issue never prevents the API
    itself from starting.
    """
    logger.info("Initializing core data...")
    db = SessionLocal()
    try:
        inserted = seed_default_segments(db)
        if inserted:
            logger.info("Seeded %d default cdp_segments row(s) across tenant(s).", inserted)
        if seed_root_admin_user(db):
            logger.info("Seeded root admin sys_user '%s' for tenant %s.", settings.default_root_username, DEFAULT_TENANT_ID)
    except Exception:
        logger.exception("init_core_data failed (continuing startup without seed data)")
    finally:
        db.close()
    logger.info("Core data initialization complete.")
