"""AI-native Customer Persona Resolution Engine.

Goes beyond identity *matching* (resolver.py: "is this the same raw touch as
that master profile?") to identity *understanding*: turns an already-resolved
``cdp_master_profiles`` row into a scored, explainable, versioned "persona" --
who this person actually IS (behavior/engagement/financial/loyalty/
relationship/risk) -- matched against a SHARED ``cdp_persona_archetypes``
row (``persona_code``/``persona_name``/``persona_summary`` live there, not
per-profile) via a versioned ``cdp_customer_personas`` MATCH row, plus
``cdp_persona_features`` + ``cdp_persona_score_details``, with
``cdp_persona_history`` recording any material change over time. Many master
profiles can share one archetype -- that many-to-many fan-in is what powers
the Persona Management admin UI's "Total Matched Profiles" per archetype.

Design goals (mirrors persona.py's philosophy):
    - Pure, DB-free scoring: ``compute_persona()`` takes a plain dict
      (an already-fetched ``cdp_master_profiles`` row) and returns a
      ``PersonaComputation`` -- fully unit-testable without a database.
    - Never raises: ``PersonaResolutionEngine.resolve_persona()`` (the only
      DB-touching entry point) catches every exception -- a persona
      computation bug must never abort or roll back an in-flight CIR
      resolution batch (see resolver.py's ``run_resolution_batch``).
    - No PII ever reaches the LLM: persona_name/persona_summary generation
      (persona.py) is only ever given non-PII, already-computed statistics.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from . import persona
from .persona import generate_persona_name, generate_persona_summary, primary_role_label

logger = logging.getLogger(__name__)

MODEL_VERSION = "persona-engine-v1"


# =============================================================================
# PERSONA CONFIG DEFAULTS + LOADER
# =============================================================================
# Database-backed configuration registry for persona scoring knobs.
# The engine keeps all module-level constants below, but values are now
# sourced from cdp_persona_config (with these defaults as fallback).
PERSONA_CONFIG_DEFAULTS: Dict[str, Any] = {
    # Risk level thresholds
    "RISK_LEVEL_CRITICAL_THRESHOLD": 80.0,
    "RISK_LEVEL_HIGH_THRESHOLD": 60.0,
    "RISK_LEVEL_MEDIUM_THRESHOLD": 40.0,
    # Lifecycle behavior score baselines
    "LIFECYCLE_BEHAVIOR_PROSPECT_BASE": 20.0,
    "LIFECYCLE_BEHAVIOR_LEAD_BASE": 40.0,
    "LIFECYCLE_BEHAVIOR_CUSTOMER_BASE": 65.0,
    "LIFECYCLE_BEHAVIOR_VIP_BASE": 95.0,
    "LIFECYCLE_BEHAVIOR_DORMANT_BASE": 30.0,
    "LIFECYCLE_BEHAVIOR_CHURN_RISK_BASE": 35.0,
    "LIFECYCLE_BEHAVIOR_DEFAULT_BASE": 30.0,
    # Engagement configuration
    "ENGAGEMENT_RECENCY_UNKNOWN_SCORE": 30.0,
    "ENGAGEMENT_RECENCY_RECENT_7D_SCORE": 100.0,
    "ENGAGEMENT_RECENCY_RECENT_30D_SCORE": 80.0,
    "ENGAGEMENT_RECENCY_RECENT_90D_SCORE": 50.0,
    "ENGAGEMENT_RECENCY_RECENT_180D_SCORE": 25.0,
    "ENGAGEMENT_RECENCY_STALE_SCORE": 10.0,
    "ENGAGEMENT_RECENCY_THRESHOLD_7D": 7,
    "ENGAGEMENT_RECENCY_THRESHOLD_30D": 30,
    "ENGAGEMENT_RECENCY_THRESHOLD_90D": 90,
    "ENGAGEMENT_RECENCY_THRESHOLD_180D": 180,
    "ENGAGEMENT_CHANNEL_WEIGHT_PER_SYSTEM": 10.0,
    "ENGAGEMENT_CHANNEL_BONUS_CAP": 30.0,
    "ENGAGEMENT_RECENCY_WEIGHT": 0.7,
    # Financial configuration
    "FINANCIAL_CLV_REFERENCE_DEFAULT": 5000.0,
    "FINANCIAL_SCORE_MULTIPLIER": 100.0,
    # Loyalty configuration
    "LOYALTY_TIER_PLATINUM_BASE": 100.0,
    "LOYALTY_TIER_GOLD_BASE": 80.0,
    "LOYALTY_TIER_SILVER_BASE": 60.0,
    "LOYALTY_TIER_BRONZE_BASE": 40.0,
    "LOYALTY_TIER_DEFAULT_BASE": 20.0,
    "LOYALTY_TENURE_WEIGHT": 0.8,
    "LOYALTY_TENURE_BONUS_PER_YEAR": 20.0,
    "LOYALTY_TENURE_BONUS_CAP": 20.0,
    "LOYALTY_TENURE_REFERENCE_DAYS": 365.0,
    # Relationship configuration
    "RELATIONSHIP_CHANNEL_WEIGHT_PER_SYSTEM": 20.0,
    "RELATIONSHIP_CHANNEL_BONUS_CAP": 60.0,
    "RELATIONSHIP_CONTACT_WEIGHT_PER_CONTACT": 10.0,
    "RELATIONSHIP_CONTACT_BONUS_CAP": 40.0,
    # Risk scoring configuration
    "RISK_SCORE_CHURN_MULTIPLIER": 100.0,
    "RISK_SCORE_DEFAULT_CHURN_BASE": 20.0,
    "RISK_SEGMENT_BONUS_LOW": 0.0,
    "RISK_SEGMENT_BONUS_MEDIUM": 15.0,
    "RISK_SEGMENT_BONUS_HIGH": 30.0,
    "RISK_SEGMENT_BONUS_CRITICAL": 45.0,
    "KYC_STATUS_BONUS_VERIFIED": 0.0,
    "KYC_STATUS_BONUS_PENDING": 10.0,
    "KYC_STATUS_BONUS_UNVERIFIED": 20.0,
    "KYC_STATUS_BONUS_REJECTED": 40.0,
    # Customer value tier thresholds
    "VALUE_TIER_CHAMPION_THRESHOLD": 80.0,
    "VALUE_TIER_HIGH_VALUE_THRESHOLD": 60.0,
    "VALUE_TIER_GROWTH_POTENTIAL_THRESHOLD": 35.0,
    # Persona scoring weights
    "SCORE_WEIGHT_BEHAVIOR": 0.20,
    "SCORE_WEIGHT_ENGAGEMENT": 0.20,
    "SCORE_WEIGHT_FINANCIAL": 0.20,
    "SCORE_WEIGHT_LOYALTY": 0.15,
    "SCORE_WEIGHT_RELATIONSHIP": 0.10,
    "SCORE_WEIGHT_RISK": 0.15,
    "SCORE_WEIGHTS_POSITIVE_SUM": 0.85,
    # History tracking
    "PERSONA_HISTORY_SCORE_DELTA_THRESHOLD": 5.0,
}

_PERSONA_CONFIG_INT_KEYS = {
    "ENGAGEMENT_RECENCY_THRESHOLD_7D",
    "ENGAGEMENT_RECENCY_THRESHOLD_30D",
    "ENGAGEMENT_RECENCY_THRESHOLD_90D",
    "ENGAGEMENT_RECENCY_THRESHOLD_180D",
}


def _parse_persona_config_value(raw_value: Any, data_type: Optional[str], default_value: Any) -> Any:
    if raw_value is None:
        return default_value

    normalized_type = (data_type or "").strip().upper()
    if normalized_type in ("INTEGER", "INT"):
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            return default_value
    if normalized_type in ("NUMERIC", "DECIMAL", "FLOAT", "DOUBLE"):
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return default_value
    if normalized_type in ("BOOLEAN", "BOOL"):
        if isinstance(raw_value, bool):
            return raw_value
        lowered = str(raw_value).strip().lower()
        if lowered in ("1", "true", "t", "yes", "y", "on"):
            return True
        if lowered in ("0", "false", "f", "no", "n", "off"):
            return False
        return default_value

    # Inference fallback when data_type is missing/inconsistent in seeded data.
    if isinstance(default_value, int):
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            return default_value
    if isinstance(default_value, float):
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return default_value
    return raw_value


def load_persona_config(cursor, schema: str = "customer360") -> Dict[str, Any]:
    config = dict(PERSONA_CONFIG_DEFAULTS)
    query = f"""
        SELECT config_key, config_value, data_type
        FROM {schema}.cdp_persona_config
        WHERE is_active = TRUE;
    """

    try:
        cursor.execute(query)
        rows = cursor.fetchall() or []
    except Exception:
        logger.warning("Could not load cdp_persona_config; using in-code defaults.", exc_info=True)
        return config

    for row in rows:
        key = row.get("config_key") if isinstance(row, dict) else None
        if not key or key not in PERSONA_CONFIG_DEFAULTS:
            continue
        config[key] = _parse_persona_config_value(
            row.get("config_value"),
            row.get("data_type"),
            PERSONA_CONFIG_DEFAULTS[key],
        )

    return config


def apply_persona_config(persona_config: Dict[str, Any]) -> None:
    """Binds module-level constants/maps from a resolved config dict.

    Keeps all existing constant names available for backwards compatibility.
    """
    global RISK_LEVEL_CRITICAL_THRESHOLD
    global RISK_LEVEL_HIGH_THRESHOLD
    global RISK_LEVEL_MEDIUM_THRESHOLD
    global LIFECYCLE_BEHAVIOR_PROSPECT_BASE
    global LIFECYCLE_BEHAVIOR_LEAD_BASE
    global LIFECYCLE_BEHAVIOR_CUSTOMER_BASE
    global LIFECYCLE_BEHAVIOR_VIP_BASE
    global LIFECYCLE_BEHAVIOR_DORMANT_BASE
    global LIFECYCLE_BEHAVIOR_CHURN_RISK_BASE
    global LIFECYCLE_BEHAVIOR_DEFAULT_BASE
    global ENGAGEMENT_RECENCY_UNKNOWN_SCORE
    global ENGAGEMENT_RECENCY_RECENT_7D_SCORE
    global ENGAGEMENT_RECENCY_RECENT_30D_SCORE
    global ENGAGEMENT_RECENCY_RECENT_90D_SCORE
    global ENGAGEMENT_RECENCY_RECENT_180D_SCORE
    global ENGAGEMENT_RECENCY_STALE_SCORE
    global ENGAGEMENT_RECENCY_THRESHOLD_7D
    global ENGAGEMENT_RECENCY_THRESHOLD_30D
    global ENGAGEMENT_RECENCY_THRESHOLD_90D
    global ENGAGEMENT_RECENCY_THRESHOLD_180D
    global ENGAGEMENT_CHANNEL_WEIGHT_PER_SYSTEM
    global ENGAGEMENT_CHANNEL_BONUS_CAP
    global ENGAGEMENT_RECENCY_WEIGHT
    global FINANCIAL_CLV_REFERENCE_DEFAULT
    global FINANCIAL_SCORE_MULTIPLIER
    global LOYALTY_TIER_PLATINUM_BASE
    global LOYALTY_TIER_GOLD_BASE
    global LOYALTY_TIER_SILVER_BASE
    global LOYALTY_TIER_BRONZE_BASE
    global LOYALTY_TIER_DEFAULT_BASE
    global LOYALTY_TENURE_WEIGHT
    global LOYALTY_TENURE_BONUS_PER_YEAR
    global LOYALTY_TENURE_BONUS_CAP
    global LOYALTY_TENURE_REFERENCE_DAYS
    global RELATIONSHIP_CHANNEL_WEIGHT_PER_SYSTEM
    global RELATIONSHIP_CHANNEL_BONUS_CAP
    global RELATIONSHIP_CONTACT_WEIGHT_PER_CONTACT
    global RELATIONSHIP_CONTACT_BONUS_CAP
    global RISK_SCORE_CHURN_MULTIPLIER
    global RISK_SCORE_DEFAULT_CHURN_BASE
    global VALUE_TIER_CHAMPION_THRESHOLD
    global VALUE_TIER_HIGH_VALUE_THRESHOLD
    global VALUE_TIER_GROWTH_POTENTIAL_THRESHOLD
    global SCORE_WEIGHT_BEHAVIOR
    global SCORE_WEIGHT_ENGAGEMENT
    global SCORE_WEIGHT_FINANCIAL
    global SCORE_WEIGHT_LOYALTY
    global SCORE_WEIGHT_RELATIONSHIP
    global SCORE_WEIGHT_RISK
    global SCORE_WEIGHTS_POSITIVE_SUM
    global PERSONA_HISTORY_SCORE_DELTA_THRESHOLD
    global _LIFECYCLE_BEHAVIOR_BASE
    global _MEMBERSHIP_TIER_BASE
    global _RISK_SEGMENT_BONUS
    global _KYC_STATUS_BONUS
    global _SCORE_WEIGHTS

    def _cfg(key: str) -> Any:
        return persona_config.get(key, PERSONA_CONFIG_DEFAULTS[key])

    RISK_LEVEL_CRITICAL_THRESHOLD = float(_cfg("RISK_LEVEL_CRITICAL_THRESHOLD"))
    RISK_LEVEL_HIGH_THRESHOLD = float(_cfg("RISK_LEVEL_HIGH_THRESHOLD"))
    RISK_LEVEL_MEDIUM_THRESHOLD = float(_cfg("RISK_LEVEL_MEDIUM_THRESHOLD"))

    LIFECYCLE_BEHAVIOR_PROSPECT_BASE = float(_cfg("LIFECYCLE_BEHAVIOR_PROSPECT_BASE"))
    LIFECYCLE_BEHAVIOR_LEAD_BASE = float(_cfg("LIFECYCLE_BEHAVIOR_LEAD_BASE"))
    LIFECYCLE_BEHAVIOR_CUSTOMER_BASE = float(_cfg("LIFECYCLE_BEHAVIOR_CUSTOMER_BASE"))
    LIFECYCLE_BEHAVIOR_VIP_BASE = float(_cfg("LIFECYCLE_BEHAVIOR_VIP_BASE"))
    LIFECYCLE_BEHAVIOR_DORMANT_BASE = float(_cfg("LIFECYCLE_BEHAVIOR_DORMANT_BASE"))
    LIFECYCLE_BEHAVIOR_CHURN_RISK_BASE = float(_cfg("LIFECYCLE_BEHAVIOR_CHURN_RISK_BASE"))
    LIFECYCLE_BEHAVIOR_DEFAULT_BASE = float(_cfg("LIFECYCLE_BEHAVIOR_DEFAULT_BASE"))

    ENGAGEMENT_RECENCY_UNKNOWN_SCORE = float(_cfg("ENGAGEMENT_RECENCY_UNKNOWN_SCORE"))
    ENGAGEMENT_RECENCY_RECENT_7D_SCORE = float(_cfg("ENGAGEMENT_RECENCY_RECENT_7D_SCORE"))
    ENGAGEMENT_RECENCY_RECENT_30D_SCORE = float(_cfg("ENGAGEMENT_RECENCY_RECENT_30D_SCORE"))
    ENGAGEMENT_RECENCY_RECENT_90D_SCORE = float(_cfg("ENGAGEMENT_RECENCY_RECENT_90D_SCORE"))
    ENGAGEMENT_RECENCY_RECENT_180D_SCORE = float(_cfg("ENGAGEMENT_RECENCY_RECENT_180D_SCORE"))
    ENGAGEMENT_RECENCY_STALE_SCORE = float(_cfg("ENGAGEMENT_RECENCY_STALE_SCORE"))
    ENGAGEMENT_RECENCY_THRESHOLD_7D = int(_cfg("ENGAGEMENT_RECENCY_THRESHOLD_7D"))
    ENGAGEMENT_RECENCY_THRESHOLD_30D = int(_cfg("ENGAGEMENT_RECENCY_THRESHOLD_30D"))
    ENGAGEMENT_RECENCY_THRESHOLD_90D = int(_cfg("ENGAGEMENT_RECENCY_THRESHOLD_90D"))
    ENGAGEMENT_RECENCY_THRESHOLD_180D = int(_cfg("ENGAGEMENT_RECENCY_THRESHOLD_180D"))
    ENGAGEMENT_CHANNEL_WEIGHT_PER_SYSTEM = float(_cfg("ENGAGEMENT_CHANNEL_WEIGHT_PER_SYSTEM"))
    ENGAGEMENT_CHANNEL_BONUS_CAP = float(_cfg("ENGAGEMENT_CHANNEL_BONUS_CAP"))
    ENGAGEMENT_RECENCY_WEIGHT = float(_cfg("ENGAGEMENT_RECENCY_WEIGHT"))

    FINANCIAL_CLV_REFERENCE_DEFAULT = float(_cfg("FINANCIAL_CLV_REFERENCE_DEFAULT"))
    FINANCIAL_SCORE_MULTIPLIER = float(_cfg("FINANCIAL_SCORE_MULTIPLIER"))

    LOYALTY_TIER_PLATINUM_BASE = float(_cfg("LOYALTY_TIER_PLATINUM_BASE"))
    LOYALTY_TIER_GOLD_BASE = float(_cfg("LOYALTY_TIER_GOLD_BASE"))
    LOYALTY_TIER_SILVER_BASE = float(_cfg("LOYALTY_TIER_SILVER_BASE"))
    LOYALTY_TIER_BRONZE_BASE = float(_cfg("LOYALTY_TIER_BRONZE_BASE"))
    LOYALTY_TIER_DEFAULT_BASE = float(_cfg("LOYALTY_TIER_DEFAULT_BASE"))
    LOYALTY_TENURE_WEIGHT = float(_cfg("LOYALTY_TENURE_WEIGHT"))
    LOYALTY_TENURE_BONUS_PER_YEAR = float(_cfg("LOYALTY_TENURE_BONUS_PER_YEAR"))
    LOYALTY_TENURE_BONUS_CAP = float(_cfg("LOYALTY_TENURE_BONUS_CAP"))
    LOYALTY_TENURE_REFERENCE_DAYS = float(_cfg("LOYALTY_TENURE_REFERENCE_DAYS"))

    RELATIONSHIP_CHANNEL_WEIGHT_PER_SYSTEM = float(_cfg("RELATIONSHIP_CHANNEL_WEIGHT_PER_SYSTEM"))
    RELATIONSHIP_CHANNEL_BONUS_CAP = float(_cfg("RELATIONSHIP_CHANNEL_BONUS_CAP"))
    RELATIONSHIP_CONTACT_WEIGHT_PER_CONTACT = float(_cfg("RELATIONSHIP_CONTACT_WEIGHT_PER_CONTACT"))
    RELATIONSHIP_CONTACT_BONUS_CAP = float(_cfg("RELATIONSHIP_CONTACT_BONUS_CAP"))

    RISK_SCORE_CHURN_MULTIPLIER = float(_cfg("RISK_SCORE_CHURN_MULTIPLIER"))
    RISK_SCORE_DEFAULT_CHURN_BASE = float(_cfg("RISK_SCORE_DEFAULT_CHURN_BASE"))

    VALUE_TIER_CHAMPION_THRESHOLD = float(_cfg("VALUE_TIER_CHAMPION_THRESHOLD"))
    VALUE_TIER_HIGH_VALUE_THRESHOLD = float(_cfg("VALUE_TIER_HIGH_VALUE_THRESHOLD"))
    VALUE_TIER_GROWTH_POTENTIAL_THRESHOLD = float(_cfg("VALUE_TIER_GROWTH_POTENTIAL_THRESHOLD"))

    SCORE_WEIGHT_BEHAVIOR = float(_cfg("SCORE_WEIGHT_BEHAVIOR"))
    SCORE_WEIGHT_ENGAGEMENT = float(_cfg("SCORE_WEIGHT_ENGAGEMENT"))
    SCORE_WEIGHT_FINANCIAL = float(_cfg("SCORE_WEIGHT_FINANCIAL"))
    SCORE_WEIGHT_LOYALTY = float(_cfg("SCORE_WEIGHT_LOYALTY"))
    SCORE_WEIGHT_RELATIONSHIP = float(_cfg("SCORE_WEIGHT_RELATIONSHIP"))
    SCORE_WEIGHT_RISK = float(_cfg("SCORE_WEIGHT_RISK"))
    SCORE_WEIGHTS_POSITIVE_SUM = float(_cfg("SCORE_WEIGHTS_POSITIVE_SUM"))

    PERSONA_HISTORY_SCORE_DELTA_THRESHOLD = float(_cfg("PERSONA_HISTORY_SCORE_DELTA_THRESHOLD"))

    _LIFECYCLE_BEHAVIOR_BASE = {
        "prospect": LIFECYCLE_BEHAVIOR_PROSPECT_BASE,
        "lead": LIFECYCLE_BEHAVIOR_LEAD_BASE,
        "customer": LIFECYCLE_BEHAVIOR_CUSTOMER_BASE,
        "vip": LIFECYCLE_BEHAVIOR_VIP_BASE,
        "dormant": LIFECYCLE_BEHAVIOR_DORMANT_BASE,
        "churn_risk": LIFECYCLE_BEHAVIOR_CHURN_RISK_BASE,
    }

    _MEMBERSHIP_TIER_BASE = {
        "platinum": LOYALTY_TIER_PLATINUM_BASE,
        "gold": LOYALTY_TIER_GOLD_BASE,
        "silver": LOYALTY_TIER_SILVER_BASE,
        "bronze": LOYALTY_TIER_BRONZE_BASE,
    }

    _RISK_SEGMENT_BONUS = {
        "low": float(_cfg("RISK_SEGMENT_BONUS_LOW")),
        "medium": float(_cfg("RISK_SEGMENT_BONUS_MEDIUM")),
        "high": float(_cfg("RISK_SEGMENT_BONUS_HIGH")),
        "critical": float(_cfg("RISK_SEGMENT_BONUS_CRITICAL")),
    }

    _KYC_STATUS_BONUS = {
        "verified": float(_cfg("KYC_STATUS_BONUS_VERIFIED")),
        "pending": float(_cfg("KYC_STATUS_BONUS_PENDING")),
        "unverified": float(_cfg("KYC_STATUS_BONUS_UNVERIFIED")),
        "rejected": float(_cfg("KYC_STATUS_BONUS_REJECTED")),
    }

    _SCORE_WEIGHTS = {
        "behavior": SCORE_WEIGHT_BEHAVIOR,
        "engagement": SCORE_WEIGHT_ENGAGEMENT,
        "financial": SCORE_WEIGHT_FINANCIAL,
        "loyalty": SCORE_WEIGHT_LOYALTY,
        "relationship": SCORE_WEIGHT_RELATIONSHIP,
        "risk": SCORE_WEIGHT_RISK,
    }

# =============================================================================
# RISK LEVEL THRESHOLDS
# =============================================================================
RISK_LEVEL_CRITICAL_THRESHOLD = float(PERSONA_CONFIG_DEFAULTS["RISK_LEVEL_CRITICAL_THRESHOLD"])
RISK_LEVEL_HIGH_THRESHOLD = float(PERSONA_CONFIG_DEFAULTS["RISK_LEVEL_HIGH_THRESHOLD"])
RISK_LEVEL_MEDIUM_THRESHOLD = float(PERSONA_CONFIG_DEFAULTS["RISK_LEVEL_MEDIUM_THRESHOLD"])

# =============================================================================
# LIFECYCLE BEHAVIOR SCORE BASELINES
# =============================================================================
LIFECYCLE_BEHAVIOR_PROSPECT_BASE = float(PERSONA_CONFIG_DEFAULTS["LIFECYCLE_BEHAVIOR_PROSPECT_BASE"])
LIFECYCLE_BEHAVIOR_LEAD_BASE = float(PERSONA_CONFIG_DEFAULTS["LIFECYCLE_BEHAVIOR_LEAD_BASE"])
LIFECYCLE_BEHAVIOR_CUSTOMER_BASE = float(PERSONA_CONFIG_DEFAULTS["LIFECYCLE_BEHAVIOR_CUSTOMER_BASE"])
LIFECYCLE_BEHAVIOR_VIP_BASE = float(PERSONA_CONFIG_DEFAULTS["LIFECYCLE_BEHAVIOR_VIP_BASE"])
LIFECYCLE_BEHAVIOR_DORMANT_BASE = float(PERSONA_CONFIG_DEFAULTS["LIFECYCLE_BEHAVIOR_DORMANT_BASE"])
LIFECYCLE_BEHAVIOR_CHURN_RISK_BASE = float(PERSONA_CONFIG_DEFAULTS["LIFECYCLE_BEHAVIOR_CHURN_RISK_BASE"])
LIFECYCLE_BEHAVIOR_DEFAULT_BASE = float(PERSONA_CONFIG_DEFAULTS["LIFECYCLE_BEHAVIOR_DEFAULT_BASE"])

# =============================================================================
# ENGAGEMENT SCORE CONFIGURATION
# =============================================================================
ENGAGEMENT_RECENCY_UNKNOWN_SCORE = float(PERSONA_CONFIG_DEFAULTS["ENGAGEMENT_RECENCY_UNKNOWN_SCORE"])
ENGAGEMENT_RECENCY_RECENT_7D_SCORE = float(PERSONA_CONFIG_DEFAULTS["ENGAGEMENT_RECENCY_RECENT_7D_SCORE"])
ENGAGEMENT_RECENCY_RECENT_30D_SCORE = float(PERSONA_CONFIG_DEFAULTS["ENGAGEMENT_RECENCY_RECENT_30D_SCORE"])
ENGAGEMENT_RECENCY_RECENT_90D_SCORE = float(PERSONA_CONFIG_DEFAULTS["ENGAGEMENT_RECENCY_RECENT_90D_SCORE"])
ENGAGEMENT_RECENCY_RECENT_180D_SCORE = float(PERSONA_CONFIG_DEFAULTS["ENGAGEMENT_RECENCY_RECENT_180D_SCORE"])
ENGAGEMENT_RECENCY_STALE_SCORE = float(PERSONA_CONFIG_DEFAULTS["ENGAGEMENT_RECENCY_STALE_SCORE"])
ENGAGEMENT_RECENCY_THRESHOLD_7D = int(PERSONA_CONFIG_DEFAULTS["ENGAGEMENT_RECENCY_THRESHOLD_7D"])
ENGAGEMENT_RECENCY_THRESHOLD_30D = int(PERSONA_CONFIG_DEFAULTS["ENGAGEMENT_RECENCY_THRESHOLD_30D"])
ENGAGEMENT_RECENCY_THRESHOLD_90D = int(PERSONA_CONFIG_DEFAULTS["ENGAGEMENT_RECENCY_THRESHOLD_90D"])
ENGAGEMENT_RECENCY_THRESHOLD_180D = int(PERSONA_CONFIG_DEFAULTS["ENGAGEMENT_RECENCY_THRESHOLD_180D"])
ENGAGEMENT_CHANNEL_WEIGHT_PER_SYSTEM = float(PERSONA_CONFIG_DEFAULTS["ENGAGEMENT_CHANNEL_WEIGHT_PER_SYSTEM"])
ENGAGEMENT_CHANNEL_BONUS_CAP = float(PERSONA_CONFIG_DEFAULTS["ENGAGEMENT_CHANNEL_BONUS_CAP"])
ENGAGEMENT_RECENCY_WEIGHT = float(PERSONA_CONFIG_DEFAULTS["ENGAGEMENT_RECENCY_WEIGHT"])

# =============================================================================
# FINANCIAL SCORE CONFIGURATION
# =============================================================================
FINANCIAL_CLV_REFERENCE_DEFAULT = float(PERSONA_CONFIG_DEFAULTS["FINANCIAL_CLV_REFERENCE_DEFAULT"])
FINANCIAL_SCORE_MULTIPLIER = float(PERSONA_CONFIG_DEFAULTS["FINANCIAL_SCORE_MULTIPLIER"])

# =============================================================================
# LOYALTY SCORE CONFIGURATION
# =============================================================================
LOYALTY_TIER_PLATINUM_BASE = float(PERSONA_CONFIG_DEFAULTS["LOYALTY_TIER_PLATINUM_BASE"])
LOYALTY_TIER_GOLD_BASE = float(PERSONA_CONFIG_DEFAULTS["LOYALTY_TIER_GOLD_BASE"])
LOYALTY_TIER_SILVER_BASE = float(PERSONA_CONFIG_DEFAULTS["LOYALTY_TIER_SILVER_BASE"])
LOYALTY_TIER_BRONZE_BASE = float(PERSONA_CONFIG_DEFAULTS["LOYALTY_TIER_BRONZE_BASE"])
LOYALTY_TIER_DEFAULT_BASE = float(PERSONA_CONFIG_DEFAULTS["LOYALTY_TIER_DEFAULT_BASE"])
LOYALTY_TENURE_WEIGHT = float(PERSONA_CONFIG_DEFAULTS["LOYALTY_TENURE_WEIGHT"])
LOYALTY_TENURE_BONUS_PER_YEAR = float(PERSONA_CONFIG_DEFAULTS["LOYALTY_TENURE_BONUS_PER_YEAR"])
LOYALTY_TENURE_BONUS_CAP = float(PERSONA_CONFIG_DEFAULTS["LOYALTY_TENURE_BONUS_CAP"])
LOYALTY_TENURE_REFERENCE_DAYS = float(PERSONA_CONFIG_DEFAULTS["LOYALTY_TENURE_REFERENCE_DAYS"])

# =============================================================================
# RELATIONSHIP SCORE CONFIGURATION
# =============================================================================
RELATIONSHIP_CHANNEL_WEIGHT_PER_SYSTEM = float(PERSONA_CONFIG_DEFAULTS["RELATIONSHIP_CHANNEL_WEIGHT_PER_SYSTEM"])
RELATIONSHIP_CHANNEL_BONUS_CAP = float(PERSONA_CONFIG_DEFAULTS["RELATIONSHIP_CHANNEL_BONUS_CAP"])
RELATIONSHIP_CONTACT_WEIGHT_PER_CONTACT = float(PERSONA_CONFIG_DEFAULTS["RELATIONSHIP_CONTACT_WEIGHT_PER_CONTACT"])
RELATIONSHIP_CONTACT_BONUS_CAP = float(PERSONA_CONFIG_DEFAULTS["RELATIONSHIP_CONTACT_BONUS_CAP"])

# =============================================================================
# RISK SCORING CONFIGURATION
# =============================================================================
RISK_SCORE_CHURN_MULTIPLIER = float(PERSONA_CONFIG_DEFAULTS["RISK_SCORE_CHURN_MULTIPLIER"])
RISK_SCORE_DEFAULT_CHURN_BASE = float(PERSONA_CONFIG_DEFAULTS["RISK_SCORE_DEFAULT_CHURN_BASE"])

# =============================================================================
# CUSTOMER VALUE TIER THRESHOLDS
# =============================================================================
VALUE_TIER_CHAMPION_THRESHOLD = float(PERSONA_CONFIG_DEFAULTS["VALUE_TIER_CHAMPION_THRESHOLD"])
VALUE_TIER_HIGH_VALUE_THRESHOLD = float(PERSONA_CONFIG_DEFAULTS["VALUE_TIER_HIGH_VALUE_THRESHOLD"])
VALUE_TIER_GROWTH_POTENTIAL_THRESHOLD = float(PERSONA_CONFIG_DEFAULTS["VALUE_TIER_GROWTH_POTENTIAL_THRESHOLD"])

# =============================================================================
# PERSONA SCORING WEIGHTS
# =============================================================================
# Positive component weights sum to 0.85; the remaining 0.15 is applied
# to (100 - risk_score) so a risk_score of 0 contributes its full 15 points
# and a risk_score of 100 contributes 0, keeping the overall persona_score
# bounded to [0, 100].
SCORE_WEIGHT_BEHAVIOR = float(PERSONA_CONFIG_DEFAULTS["SCORE_WEIGHT_BEHAVIOR"])
SCORE_WEIGHT_ENGAGEMENT = float(PERSONA_CONFIG_DEFAULTS["SCORE_WEIGHT_ENGAGEMENT"])
SCORE_WEIGHT_FINANCIAL = float(PERSONA_CONFIG_DEFAULTS["SCORE_WEIGHT_FINANCIAL"])
SCORE_WEIGHT_LOYALTY = float(PERSONA_CONFIG_DEFAULTS["SCORE_WEIGHT_LOYALTY"])
SCORE_WEIGHT_RELATIONSHIP = float(PERSONA_CONFIG_DEFAULTS["SCORE_WEIGHT_RELATIONSHIP"])
SCORE_WEIGHT_RISK = float(PERSONA_CONFIG_DEFAULTS["SCORE_WEIGHT_RISK"])
SCORE_WEIGHTS_POSITIVE_SUM = float(PERSONA_CONFIG_DEFAULTS["SCORE_WEIGHTS_POSITIVE_SUM"])

# =============================================================================
# PERSONA HISTORY TRACKING
# =============================================================================
# Minimum |old_score - new_score| delta (0-100 scale) that counts as a
# "material" persona change worth recording in cdp_persona_history.
# Avoids flooding the history table with noise from tiny score wobbles.
PERSONA_HISTORY_SCORE_DELTA_THRESHOLD = float(PERSONA_CONFIG_DEFAULTS["PERSONA_HISTORY_SCORE_DELTA_THRESHOLD"])

# Master profile columns needed to compute a persona. Deliberately excludes
# any raw PII (full_name/email/phone_number/national_id) -- persona_name is
# seeded from master_profile_id itself (see compute_persona), never from PII.
MASTER_PROFILE_SELECT_COLUMNS = (
    "master_profile_id",
    "domain",
    "lifecycle_stage",
    "customer_since",
    "last_activity_at",
    "preferred_channel",
    "source_systems",
    "secondary_emails",
    "secondary_phones",
    "historical_clv",
    "predictive_clv",
    "engagement_score",
    "churn_probability",
    "churn_risk_tier",
    "identity_confidence_score",
    "profile_completeness_score",
    "is_hashed",
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    value = (value or "").strip().lower()
    value = _SLUG_RE.sub("_", value)
    return value.strip("_") or "persona"


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clip(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _days_since(dt: Optional[datetime]) -> Optional[float]:
    if dt is None:
        return None
    now = datetime.now(timezone.utc) if getattr(dt, "tzinfo", None) is not None else datetime.now()
    delta = now - dt
    return max(delta.total_seconds() / 86400.0, 0.0)


# ---------------------------------------------------------------------------
# Component score calculators (each 0-100, pure functions of a master profile
# row snapshot -- no DB access).
# ---------------------------------------------------------------------------

_LIFECYCLE_BEHAVIOR_BASE = {
    "prospect": LIFECYCLE_BEHAVIOR_PROSPECT_BASE,
    "lead": LIFECYCLE_BEHAVIOR_LEAD_BASE,
    "customer": LIFECYCLE_BEHAVIOR_CUSTOMER_BASE,
    "vip": LIFECYCLE_BEHAVIOR_VIP_BASE,
    "dormant": LIFECYCLE_BEHAVIOR_DORMANT_BASE,
    "churn_risk": LIFECYCLE_BEHAVIOR_CHURN_RISK_BASE,
}


def compute_behavior_score(master_profile: Dict[str, Any]) -> float:
    lifecycle_stage = (master_profile.get("lifecycle_stage") or "").lower()
    base = _LIFECYCLE_BEHAVIOR_BASE.get(lifecycle_stage, LIFECYCLE_BEHAVIOR_DEFAULT_BASE)
    existing_engagement = master_profile.get("engagement_score")
    if existing_engagement is not None:
        base = (base + _to_float(existing_engagement)) / 2.0
    return _clip(base)


def compute_engagement_score(master_profile: Dict[str, Any]) -> float:
    recency_days = _days_since(master_profile.get("last_activity_at"))
    if recency_days is None:
        recency_component = ENGAGEMENT_RECENCY_UNKNOWN_SCORE
    elif recency_days <= ENGAGEMENT_RECENCY_THRESHOLD_7D:
        recency_component = ENGAGEMENT_RECENCY_RECENT_7D_SCORE
    elif recency_days <= ENGAGEMENT_RECENCY_THRESHOLD_30D:
        recency_component = ENGAGEMENT_RECENCY_RECENT_30D_SCORE
    elif recency_days <= ENGAGEMENT_RECENCY_THRESHOLD_90D:
        recency_component = ENGAGEMENT_RECENCY_RECENT_90D_SCORE
    elif recency_days <= ENGAGEMENT_RECENCY_THRESHOLD_180D:
        recency_component = ENGAGEMENT_RECENCY_RECENT_180D_SCORE
    else:
        recency_component = ENGAGEMENT_RECENCY_STALE_SCORE
    channel_bonus = min(
        len(master_profile.get("source_systems") or []) * ENGAGEMENT_CHANNEL_WEIGHT_PER_SYSTEM,
        ENGAGEMENT_CHANNEL_BONUS_CAP,
    )
    return _clip(recency_component * ENGAGEMENT_RECENCY_WEIGHT + channel_bonus)


def compute_financial_score(master_profile: Dict[str, Any], clv_reference: float = FINANCIAL_CLV_REFERENCE_DEFAULT) -> float:
    clv = master_profile.get("predictive_clv")
    if clv is None:
        clv = master_profile.get("historical_clv")
    clv = _to_float(clv)
    if clv_reference <= 0:
        return 0.0
    return _clip((clv / clv_reference) * FINANCIAL_SCORE_MULTIPLIER)


_MEMBERSHIP_TIER_BASE = {
    "platinum": LOYALTY_TIER_PLATINUM_BASE,
    "gold": LOYALTY_TIER_GOLD_BASE,
    "silver": LOYALTY_TIER_SILVER_BASE,
    "bronze": LOYALTY_TIER_BRONZE_BASE,
}


def compute_loyalty_score(master_profile: Dict[str, Any]) -> float:
    tier = (master_profile.get("membership_tier") or "").lower()
    base = _MEMBERSHIP_TIER_BASE.get(tier, LOYALTY_TIER_DEFAULT_BASE)
    customer_since = master_profile.get("customer_since")
    tenure_days = (date.today() - customer_since).days if customer_since is not None else 0
    tenure_bonus = min(
        max(tenure_days, 0) / LOYALTY_TENURE_REFERENCE_DAYS * LOYALTY_TENURE_BONUS_PER_YEAR,
        LOYALTY_TENURE_BONUS_CAP,
    )
    return _clip(base * LOYALTY_TENURE_WEIGHT + tenure_bonus)


def compute_relationship_score(master_profile: Dict[str, Any]) -> float:
    channel_component = min(
        len(master_profile.get("source_systems") or []) * RELATIONSHIP_CHANNEL_WEIGHT_PER_SYSTEM,
        RELATIONSHIP_CHANNEL_BONUS_CAP,
    )
    secondary_contacts = len(master_profile.get("secondary_emails") or []) + len(
        master_profile.get("secondary_phones") or []
    )
    contact_component = min(secondary_contacts * RELATIONSHIP_CONTACT_WEIGHT_PER_CONTACT, RELATIONSHIP_CONTACT_BONUS_CAP)
    return _clip(channel_component + contact_component)


_RISK_SEGMENT_BONUS = {
    "low": float(PERSONA_CONFIG_DEFAULTS["RISK_SEGMENT_BONUS_LOW"]),
    "medium": float(PERSONA_CONFIG_DEFAULTS["RISK_SEGMENT_BONUS_MEDIUM"]),
    "high": float(PERSONA_CONFIG_DEFAULTS["RISK_SEGMENT_BONUS_HIGH"]),
    "critical": float(PERSONA_CONFIG_DEFAULTS["RISK_SEGMENT_BONUS_CRITICAL"]),
}
_KYC_STATUS_BONUS = {
    "verified": float(PERSONA_CONFIG_DEFAULTS["KYC_STATUS_BONUS_VERIFIED"]),
    "pending": float(PERSONA_CONFIG_DEFAULTS["KYC_STATUS_BONUS_PENDING"]),
    "unverified": float(PERSONA_CONFIG_DEFAULTS["KYC_STATUS_BONUS_UNVERIFIED"]),
    "rejected": float(PERSONA_CONFIG_DEFAULTS["KYC_STATUS_BONUS_REJECTED"]),
}


def compute_risk_score(master_profile: Dict[str, Any]) -> float:
    churn_probability = master_profile.get("churn_probability")
    base = (
        _to_float(churn_probability) * RISK_SCORE_CHURN_MULTIPLIER
        if churn_probability is not None
        else RISK_SCORE_DEFAULT_CHURN_BASE
    )
    base += _RISK_SEGMENT_BONUS.get((master_profile.get("risk_segment") or "").lower(), 0.0)
    base += _KYC_STATUS_BONUS.get((master_profile.get("kyc_status") or "").lower(), 0.0)
    return _clip(base)


# Weights applied in compute_persona_score(). Positive component weights sum
# to 0.85; the remaining 0.15 is applied to (100 - risk_score) so a
# risk_score of 0 contributes its full 15 points and a risk_score of 100
# contributes 0 -- keeping the overall persona_score bounded to [0, 100].
_SCORE_WEIGHTS = {
    "behavior": SCORE_WEIGHT_BEHAVIOR,
    "engagement": SCORE_WEIGHT_ENGAGEMENT,
    "financial": SCORE_WEIGHT_FINANCIAL,
    "loyalty": SCORE_WEIGHT_LOYALTY,
    "relationship": SCORE_WEIGHT_RELATIONSHIP,
    "risk": SCORE_WEIGHT_RISK,
}

# Bind defaults once at import time; runtime DB overrides can re-apply.
apply_persona_config(PERSONA_CONFIG_DEFAULTS)


def compute_persona_score(scores: Dict[str, float]) -> float:
    positive_weighted = sum(
        scores[key] * _SCORE_WEIGHTS[key] for key in ("behavior", "engagement", "financial", "loyalty", "relationship")
    )
    risk_weighted = (100.0 - scores["risk"]) * SCORE_WEIGHT_RISK
    return _clip(positive_weighted + risk_weighted)


def compute_customer_value_tier(scores: Dict[str, float]) -> str:
    value_index = (scores["financial"] + scores["loyalty"]) / 2.0
    if value_index >= VALUE_TIER_CHAMPION_THRESHOLD:
        return "champion"
    if value_index >= VALUE_TIER_HIGH_VALUE_THRESHOLD:
        return "high_value"
    if value_index >= VALUE_TIER_GROWTH_POTENTIAL_THRESHOLD:
        return "growth_potential"
    return "standard"


def compute_risk_level(risk_score: float) -> str:
    if risk_score >= RISK_LEVEL_CRITICAL_THRESHOLD:
        return "critical"
    if risk_score >= RISK_LEVEL_HIGH_THRESHOLD:
        return "high"
    if risk_score >= RISK_LEVEL_MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def compute_next_best_action(*, lifecycle_stage: Optional[str], value_tier: str, risk_level: str) -> str:
    lifecycle_stage = (lifecycle_stage or "").lower()
    if risk_level in ("high", "critical") and lifecycle_stage in ("customer", "vip"):
        return "Trigger a retention/win-back campaign to reduce churn risk."
    if lifecycle_stage == "dormant":
        return "Send a re-engagement / win-back offer."
    if lifecycle_stage == "prospect":
        return "Send an acquisition offer to convert to a qualified lead."
    if lifecycle_stage == "lead":
        return "Nurture with onboarding content to drive first purchase."
    if value_tier in ("champion", "high_value") and risk_level in ("low", "medium"):
        return "Offer a loyalty upsell or premium tier upgrade."
    return "Continue standard engagement cadence."


def compute_persona_category(domain: str, value_tier: str) -> str:
    role = primary_role_label(domain)
    tier_label = value_tier.replace("_", " ").title()
    return f"{tier_label} {role}"


def compute_persona_code(domain: str, value_tier: str, lifecycle_stage: Optional[str]) -> str:
    return _slugify(f"{domain}_{value_tier}_{lifecycle_stage or 'unscored'}")


def _build_features(master_profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    features: List[Dict[str, Any]] = []

    def _add(code, name, feature_type, *, numeric=None, text=None, boolean=None):
        features.append(
            {
                "feature_code": code,
                "feature_name": name,
                "feature_type": feature_type,
                "numeric_value": numeric,
                "text_value": text,
                "boolean_value": boolean,
            }
        )

    customer_since = master_profile.get("customer_since")
    tenure_days = (date.today() - customer_since).days if customer_since is not None else None
    _add("tenure_days", "Customer Tenure (days)", "numeric", numeric=tenure_days)
    _add(
        "source_system_count",
        "Number of Source Systems",
        "numeric",
        numeric=len(master_profile.get("source_systems") or []),
    )
    _add(
        "secondary_contact_count",
        "Number of Secondary Contacts",
        "numeric",
        numeric=len(master_profile.get("secondary_emails") or []) + len(master_profile.get("secondary_phones") or []),
    )
    _add(
        "last_activity_recency_days",
        "Days Since Last Activity",
        "numeric",
        numeric=_days_since(master_profile.get("last_activity_at")),
    )
    if master_profile.get("membership_tier") is not None:
        _add("membership_tier", "Membership Tier", "text", text=master_profile.get("membership_tier"))
    if master_profile.get("kyc_status") is not None:
        _add("kyc_status", "KYC Status", "text", text=master_profile.get("kyc_status"))
    _add("is_hashed", "PII Hashed", "boolean", boolean=bool(master_profile.get("is_hashed")))
    if master_profile.get("historical_clv") is not None:
        _add("historical_clv", "Historical CLV", "numeric", numeric=_to_float(master_profile.get("historical_clv")))
    if master_profile.get("predictive_clv") is not None:
        _add("predictive_clv", "Predictive CLV", "numeric", numeric=_to_float(master_profile.get("predictive_clv")))
    if master_profile.get("churn_probability") is not None:
        _add(
            "churn_probability",
            "Churn Probability",
            "numeric",
            numeric=_to_float(master_profile.get("churn_probability")),
        )
    return features


def _score_detail_rows(computation: "PersonaComputation"):
    return [
        (
            "behavior",
            computation.behavior_score,
            _SCORE_WEIGHTS["behavior"],
            "lifecycle_stage base blended with any pre-existing engagement_score",
            "Derived from the profile's lifecycle_stage and any pre-existing engagement_score.",
        ),
        (
            "engagement",
            computation.engagement_score,
            _SCORE_WEIGHTS["engagement"],
            "recency(last_activity_at) * 0.7 + channel_count_bonus",
            "Recency of last_activity_at plus a bonus for the number of distinct source_systems.",
        ),
        (
            "financial",
            computation.financial_score,
            _SCORE_WEIGHTS["financial"],
            "min(100, (predictive_clv or historical_clv) / reference_clv * 100)",
            "Scaled from predictive_clv (falling back to historical_clv).",
        ),
        (
            "loyalty",
            computation.loyalty_score,
            _SCORE_WEIGHTS["loyalty"],
            "membership_tier_base * 0.8 + tenure_bonus",
            "Membership tier plus a tenure (customer_since) bonus.",
        ),
        (
            "relationship",
            computation.relationship_score,
            _SCORE_WEIGHTS["relationship"],
            "channel_breadth_bonus + secondary_contact_count_bonus",
            "Breadth of source_systems plus number of secondary emails/phones.",
        ),
        (
            "risk",
            computation.risk_score,
            _SCORE_WEIGHTS["risk"],
            "churn_probability * 100 + risk_segment_bonus + kyc_status_bonus",
            "Churn probability blended with risk_segment and kyc_status.",
        ),
    ]


@dataclass
class PersonaComputation:
    """Pure, DB-free computation result for a single master profile snapshot."""

    persona_code: str
    persona_category: str
    customer_value_tier: str
    risk_level: str
    next_best_action: str
    persona_score: float
    behavior_score: float
    engagement_score: float
    financial_score: float
    loyalty_score: float
    relationship_score: float
    risk_score: float
    confidence_score: float
    persona_name: str
    persona_summary: str
    llm_provider: str
    llm_model: str
    lifecycle_stage: Optional[str] = None
    # How well this profile fits its persona_archetype_id's centroid (lookalike
    # match quality). No real embedding-similarity model wired up yet, so this
    # defaults to confidence_score -- a reasonable proxy until a dedicated
    # lookalike/embedding-distance computation replaces it.
    match_score: float = 0.0
    features: List[Dict[str, Any]] = field(default_factory=list)


def compute_persona(master_profile: Dict[str, Any]) -> PersonaComputation:
    """Pure function: derives every persona field from an already-resolved
    ``cdp_master_profiles`` row snapshot. No DB access -- the only I/O is the
    optional, never-raising LLM calls inside persona.py's
    generate_persona_name/generate_persona_summary. Fully unit-testable
    without a database."""
    domain = (master_profile.get("domain") or "retail").lower()
    lifecycle_stage = master_profile.get("lifecycle_stage")

    scores = {
        "behavior": compute_behavior_score(master_profile),
        "engagement": compute_engagement_score(master_profile),
        "financial": compute_financial_score(master_profile),
        "loyalty": compute_loyalty_score(master_profile),
        "relationship": compute_relationship_score(master_profile),
        "risk": compute_risk_score(master_profile),
    }
    persona_score = compute_persona_score(scores)
    value_tier = compute_customer_value_tier(scores)
    risk_level = compute_risk_level(scores["risk"])
    next_best_action = compute_next_best_action(
        lifecycle_stage=lifecycle_stage, value_tier=value_tier, risk_level=risk_level
    )
    persona_category = compute_persona_category(domain, value_tier)
    persona_code = compute_persona_code(domain, value_tier, lifecycle_stage)

    confidence_score = master_profile.get("identity_confidence_score")
    if confidence_score is not None:
        confidence_score = _to_float(confidence_score)
    else:
        confidence_score = _to_float(master_profile.get("profile_completeness_score"), default=50.0) / 100.0
    confidence_score = min(max(confidence_score, 0.0), 1.0)

    # persona_name is (re)generated unconditionally here (not just for hashed
    # profiles, unlike resolver.py's inline CHECK-constraint-satisfying logic)
    # so EVERY profile gets a readable marketing persona label. Seeded from
    # master_profile_id itself (a random UUID, already non-PII by
    # definition) rather than raw PII/device identifiers -- guarantees a
    # stable, unique-per-profile suffix without ever touching real PII.
    seed_profile = {
        "domain": domain,
        "device_id": str(master_profile.get("master_profile_id") or ""),
        "media_source": master_profile.get("preferred_channel"),
        "source_system": (master_profile.get("source_systems") or [None])[0],
    }
    persona_name = generate_persona_name(seed_profile)

    # Read via the `persona` module namespace (not copied bindings) so that
    # monkeypatching persona.GOOGLE_GENAI_API_KEY in tests -- the same thing
    # generate_persona_name/generate_persona_summary themselves read at call
    # time -- keeps this provider/model metadata consistent with what those
    # functions actually did.
    provider_configured = persona._has_configured_api_key(persona.GOOGLE_GENAI_API_KEY)
    summary_stats = {
        "domain": domain,
        "lifecycle_stage": lifecycle_stage,
        "customer_value_tier": value_tier,
        "risk_level": risk_level,
        "next_best_action": next_best_action,
        "behavior_score": scores["behavior"],
        "engagement_score": scores["engagement"],
        "financial_score": scores["financial"],
        "loyalty_score": scores["loyalty"],
        "relationship_score": scores["relationship"],
        "risk_score": scores["risk"],
    }
    persona_summary = generate_persona_summary(summary_stats)

    return PersonaComputation(
        persona_code=persona_code,
        persona_category=persona_category,
        customer_value_tier=value_tier,
        risk_level=risk_level,
        next_best_action=next_best_action,
        persona_score=round(persona_score, 2),
        behavior_score=round(scores["behavior"], 2),
        engagement_score=round(scores["engagement"], 2),
        financial_score=round(scores["financial"], 2),
        loyalty_score=round(scores["loyalty"], 2),
        relationship_score=round(scores["relationship"], 2),
        risk_score=round(scores["risk"], 2),
        confidence_score=round(confidence_score, 4),
        persona_name=persona_name,
        persona_summary=persona_summary,
        llm_provider="google-genai" if provider_configured else "offline-heuristic",
        llm_model=persona.GOOGLE_GENAI_MODEL if provider_configured else "persona-engine-rule-based-v1",
        lifecycle_stage=lifecycle_stage,
        match_score=round(confidence_score, 4),
        features=_build_features(master_profile),
    )


class PersonaResolutionEngine:
    """Computes and persists an explainable, versioned "customer persona"
    MATCH for an already-resolved master profile: turns identity *matching*
    output (``cdp_master_profiles``) into identity *understanding* -- a
    shared ``cdp_persona_archetypes`` row upserted by (tenant_id, domain,
    persona_code), plus a versioned ``cdp_customer_personas`` match row
    (+ ``cdp_persona_features`` + ``cdp_persona_score_details``), with
    ``cdp_persona_history`` recording any material change over time.

    Mirrors ``CustomerIdentityResolver``'s style: stateless w.r.t. the DB
    connection (a psycopg2 cursor is passed into every method), safe to unit
    test without a real database. The only public entry point
    (``resolve_persona``) NEVER raises -- a persona-computation bug must
    never abort or roll back an in-flight CIR resolution batch (see
    resolver.py's ``run_resolution_batch``).
    """

    # Minimum |old_score - new_score| delta (0-100 scale) that counts as a
    # "material" persona change worth recording in cdp_persona_history --
    # avoids flooding the history table with noise from tiny score wobbles.
    HISTORY_SCORE_DELTA_THRESHOLD = PERSONA_HISTORY_SCORE_DELTA_THRESHOLD

    def __init__(
        self,
        schema: str = "customer360",
        config_cache_ttl_seconds: int = 60,
    ):
        self.schema = schema
        # Avoid re-querying cdp_persona_config for every single profile in a
        # CIR batch while still allowing periodic runtime refresh.
        self._config_cache_ttl_seconds = max(config_cache_ttl_seconds, 0)
        self._cached_persona_config: Optional[Dict[str, Any]] = None
        self._cached_persona_config_loaded_at: Optional[datetime] = None

    def _table(self, name: str) -> str:
        return f"{self.schema}.{name}" if self.schema else name

    def invalidate_config_cache(self) -> None:
        """Clears in-memory persona config cache (useful for tests/debugging)."""
        self._cached_persona_config = None
        self._cached_persona_config_loaded_at = None

    def _config_cache_is_fresh(self) -> bool:
        if self._cached_persona_config is None or self._cached_persona_config_loaded_at is None:
            return False
        if self._config_cache_ttl_seconds == 0:
            return False
        age_seconds = (datetime.now(timezone.utc) - self._cached_persona_config_loaded_at).total_seconds()
        return age_seconds < self._config_cache_ttl_seconds

    def _ensure_runtime_persona_config(self, cursor) -> None:
        """Loads and applies persona runtime config with TTL-based caching."""
        if self._config_cache_is_fresh():
            apply_persona_config(self._cached_persona_config or PERSONA_CONFIG_DEFAULTS)
            return

        config = load_persona_config(cursor, schema=self.schema)
        apply_persona_config(config)
        self._cached_persona_config = config
        self._cached_persona_config_loaded_at = datetime.now(timezone.utc)

    def _fetch_master_profile(self, cursor, tenant_id, master_profile_id) -> Optional[Dict[str, Any]]:
        columns = ", ".join(f"m.{col}" for col in MASTER_PROFILE_SELECT_COLUMNS)
        query = f"""
            SELECT
                {columns},
                dp.domain_attributes ->> 'membership_tier' AS membership_tier,
                dp.domain_attributes ->> 'risk_segment' AS risk_segment,
                dp.domain_attributes ->> 'kyc_status' AS kyc_status
            FROM {self._table('cdp_master_profiles')} m
            LEFT JOIN {self._table('sys_domain')} d
                ON d.domain_code = m.domain
            LEFT JOIN {self._table('cdp_domain_profiles')} dp
                ON dp.tenant_id = m.tenant_id
               AND dp.master_profile_id = m.master_profile_id
               AND dp.domain_id = d.domain_id
            WHERE m.master_profile_id = %s AND m.tenant_id = %s;
        """
        cursor.execute(query, (master_profile_id, tenant_id))
        return cursor.fetchone()

    def _fetch_current_persona(self, cursor, tenant_id, master_profile_id) -> Optional[Dict[str, Any]]:
        query = f"""
            SELECT cp.persona_id, pa.persona_name, cp.persona_score
            FROM {self._table('cdp_customer_personas')} cp
            JOIN {self._table('cdp_persona_archetypes')} pa
                ON pa.persona_archetype_id = cp.persona_archetype_id
            WHERE cp.tenant_id = %s AND cp.master_profile_id = %s AND cp.is_active = TRUE
            ORDER BY cp.computed_at DESC
            LIMIT 1;
        """
        cursor.execute(query, (tenant_id, master_profile_id))
        return cursor.fetchone()

    def _upsert_archetype(
        self, cursor, tenant_id, domain, computation: PersonaComputation
    ) -> Any:
        """Upserts the SHARED persona archetype (tenant_id, domain,
        persona_code) this profile matches -- many master profiles can
        share the same archetype row, which is what makes the persona
        relationship many-to-many instead of one row per profile."""
        query = f"""
            INSERT INTO {self._table('cdp_persona_archetypes')}
                (tenant_id, domain, persona_code, persona_name, persona_category,
                 persona_summary, llm_provider, llm_model)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, domain, persona_code) DO UPDATE SET
                persona_name = EXCLUDED.persona_name,
                persona_category = EXCLUDED.persona_category,
                persona_summary = EXCLUDED.persona_summary,
                llm_provider = EXCLUDED.llm_provider,
                llm_model = EXCLUDED.llm_model,
                updated_at = NOW()
            RETURNING persona_archetype_id;
        """
        cursor.execute(
            query,
            (
                tenant_id,
                domain,
                computation.persona_code,
                computation.persona_name,
                computation.persona_category,
                computation.persona_summary,
                computation.llm_provider,
                computation.llm_model,
            ),
        )
        return cursor.fetchone()["persona_archetype_id"]

    def _next_computed_version(self, cursor, tenant_id, master_profile_id, persona_archetype_id) -> int:
        query = f"""
            SELECT COALESCE(MAX(computed_version), 0) + 1 AS next_version
            FROM {self._table('cdp_customer_personas')}
            WHERE tenant_id = %s AND master_profile_id = %s AND persona_archetype_id = %s;
        """
        cursor.execute(query, (tenant_id, master_profile_id, persona_archetype_id))
        row = cursor.fetchone()
        return int(row["next_version"]) if row else 1

    def _deactivate_previous_personas(self, cursor, tenant_id, master_profile_id) -> None:
        query = f"""
            UPDATE {self._table('cdp_customer_personas')}
            SET is_active = FALSE
            WHERE tenant_id = %s AND master_profile_id = %s AND is_active = TRUE;
        """
        cursor.execute(query, (tenant_id, master_profile_id))

    def _insert_persona(
        self,
        cursor,
        tenant_id,
        domain,
        master_profile_id,
        persona_archetype_id,
        computation: PersonaComputation,
        computed_version: int,
    ) -> Any:
        query = f"""
            INSERT INTO {self._table('cdp_customer_personas')}
                (tenant_id, domain, master_profile_id, persona_archetype_id,
                 match_score, persona_score, confidence_score,
                 behavior_score, engagement_score, financial_score, loyalty_score,
                 relationship_score, risk_score, lifecycle_stage, customer_value_tier,
                 risk_level, next_best_action,
                 computed_version, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, TRUE)
            RETURNING persona_id;
        """
        cursor.execute(
            query,
            (
                tenant_id,
                domain,
                master_profile_id,
                persona_archetype_id,
                computation.match_score,
                computation.persona_score,
                computation.confidence_score,
                computation.behavior_score,
                computation.engagement_score,
                computation.financial_score,
                computation.loyalty_score,
                computation.relationship_score,
                computation.risk_score,
                computation.lifecycle_stage,
                computation.customer_value_tier,
                computation.risk_level,
                computation.next_best_action,
                computed_version,
            ),
        )
        return cursor.fetchone()["persona_id"]

    def _insert_features(self, cursor, persona_id, features: List[Dict[str, Any]]) -> None:
        if not features:
            return
        query = f"""
            INSERT INTO {self._table('cdp_persona_features')}
                (persona_id, feature_code, feature_name, feature_type,
                 numeric_value, text_value, boolean_value)
            VALUES (%s, %s, %s, %s, %s, %s, %s);
        """
        for feat in features:
            cursor.execute(
                query,
                (
                    persona_id,
                    feat["feature_code"],
                    feat.get("feature_name"),
                    feat.get("feature_type"),
                    feat.get("numeric_value"),
                    feat.get("text_value"),
                    feat.get("boolean_value"),
                ),
            )

    def _insert_score_details(self, cursor, persona_id, computation: PersonaComputation) -> None:
        query = f"""
            INSERT INTO {self._table('cdp_persona_score_details')}
                (persona_id, score_type, score_value, score_weight, score_formula, explanation)
            VALUES (%s, %s, %s, %s, %s, %s);
        """
        for score_type, value, weight, formula, explanation in _score_detail_rows(computation):
            cursor.execute(query, (persona_id, score_type, value, weight, formula, explanation))

    def _insert_history(
        self, cursor, persona_id, old_persona: Optional[Dict[str, Any]], computation: PersonaComputation
    ) -> None:
        query = f"""
            INSERT INTO {self._table('cdp_persona_history')}
                (persona_id, old_persona_name, new_persona_name, old_score, new_score,
                 change_reason, model_version)
            VALUES (%s, %s, %s, %s, %s, %s, %s);
        """
        cursor.execute(
            query,
            (
                persona_id,
                old_persona.get("persona_name") if old_persona else None,
                computation.persona_name,
                old_persona.get("persona_score") if old_persona else None,
                computation.persona_score,
                "Recomputed after CIR resolution batch" if old_persona else "Initial persona computed",
                MODEL_VERSION,
            ),
        )

    def _update_master_profile(self, cursor, tenant_id, master_profile_id, persona_id, computation) -> None:
        query = f"""
            UPDATE {self._table('cdp_master_profiles')}
            SET current_persona_id = %s, persona_name = %s, persona_summary = %s, updated_at = NOW()
            WHERE master_profile_id = %s AND tenant_id = %s;
        """
        cursor.execute(
            query, (persona_id, computation.persona_name, computation.persona_summary, master_profile_id, tenant_id)
        )

    def _should_insert_history(
        self,
        old_persona: Optional[Dict[str, Any]],
        computation: PersonaComputation,
    ) -> bool:
        if old_persona is None:
            return True

        old_score = _to_float(old_persona.get("persona_score"))
        score_delta = abs(old_score - computation.persona_score)
        name_changed = old_persona.get("persona_name") != computation.persona_name
        return name_changed or score_delta >= self.HISTORY_SCORE_DELTA_THRESHOLD

    def resolve_persona(self, cursor, tenant_id, master_profile_id) -> Optional[Dict[str, Any]]:
        """Computes and persists a fresh persona snapshot for one master
        profile. Returns a small summary dict on success, or ``None`` if the
        profile could not be found OR anything failed along the way -- this
        method NEVER raises, so it is always safe to call from inside
        resolver.py's per-tenant transaction without risking a rollback of
        otherwise-successful identity matching work."""
        try:
            logger.debug(
                "Resolving persona for tenant_id=%s master_profile_id=%s",
                tenant_id,
                master_profile_id,
            )

            self._ensure_runtime_persona_config(cursor)
            master_profile = self._fetch_master_profile(cursor, tenant_id, master_profile_id)
            if master_profile is None:
                logger.debug(
                    "Skipped persona resolution: master profile not found for tenant_id=%s master_profile_id=%s",
                    tenant_id,
                    master_profile_id,
                )
                return None

            computation = compute_persona(master_profile)
            old_persona = self._fetch_current_persona(cursor, tenant_id, master_profile_id)
            domain = master_profile.get("domain") or "retail"
            persona_archetype_id = self._upsert_archetype(cursor, tenant_id, domain, computation)
            computed_version = self._next_computed_version(
                cursor, tenant_id, master_profile_id, persona_archetype_id
            )

            self._deactivate_previous_personas(cursor, tenant_id, master_profile_id)
            persona_id = self._insert_persona(
                cursor, tenant_id, domain, master_profile_id, persona_archetype_id, computation,
                computed_version,
            )
            self._insert_features(cursor, persona_id, computation.features)
            self._insert_score_details(cursor, persona_id, computation)

            if self._should_insert_history(old_persona, computation):
                self._insert_history(cursor, persona_id, old_persona, computation)

            self._update_master_profile(cursor, tenant_id, master_profile_id, persona_id, computation)

            logger.debug(
                "Persona resolved: tenant_id=%s master_profile_id=%s persona_id=%s code=%s score=%.2f version=%s",
                tenant_id,
                master_profile_id,
                persona_id,
                computation.persona_code,
                computation.persona_score,
                computed_version,
            )

            return {
                "persona_id": persona_id,
                "persona_archetype_id": persona_archetype_id,
                "persona_code": computation.persona_code,
                "persona_name": computation.persona_name,
                "persona_score": computation.persona_score,
                "computed_version": computed_version,
            }
        except Exception:
            logger.exception(
                "Persona resolution failed for master_profile_id=%s (tenant_id=%s); "
                "leaving prior persona/current_persona_id untouched.",
                master_profile_id,
                tenant_id,
            )
            return None
