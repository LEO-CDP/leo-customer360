"""AI-native Customer Persona Resolution Engine.

Goes beyond identity *matching* (resolver.py: "is this the same raw touch as
that master profile?") to identity *understanding*: turns an already-resolved
``cdp_master_profiles`` row into a scored, explainable, versioned "persona" --
who this person actually IS (behavior/engagement/financial/loyalty/
relationship/risk) -- persisted into ``cdp_customer_personas`` +
``cdp_persona_features`` + ``cdp_persona_score_details``, with
``cdp_persona_history`` recording any material change over time.

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

# Master profile columns needed to compute a persona. Deliberately excludes
# any raw PII (full_name/email/phone_number/national_id) -- persona_name is
# seeded from master_profile_id itself (see compute_persona), never from PII.
MASTER_PROFILE_SELECT_COLUMNS = (
    "master_profile_id",
    "domain",
    "lifecycle_stage",
    "membership_tier",
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
    "risk_segment",
    "kyc_status",
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
    "prospect": 20.0,
    "lead": 40.0,
    "customer": 65.0,
    "vip": 95.0,
    "dormant": 30.0,
    "churn_risk": 35.0,
}


def compute_behavior_score(master_profile: Dict[str, Any]) -> float:
    lifecycle_stage = (master_profile.get("lifecycle_stage") or "").lower()
    base = _LIFECYCLE_BEHAVIOR_BASE.get(lifecycle_stage, 30.0)
    existing_engagement = master_profile.get("engagement_score")
    if existing_engagement is not None:
        base = (base + _to_float(existing_engagement)) / 2.0
    return _clip(base)


def compute_engagement_score(master_profile: Dict[str, Any]) -> float:
    recency_days = _days_since(master_profile.get("last_activity_at"))
    if recency_days is None:
        recency_component = 30.0
    elif recency_days <= 7:
        recency_component = 100.0
    elif recency_days <= 30:
        recency_component = 80.0
    elif recency_days <= 90:
        recency_component = 50.0
    elif recency_days <= 180:
        recency_component = 25.0
    else:
        recency_component = 10.0
    channel_bonus = min(len(master_profile.get("source_systems") or []) * 10.0, 30.0)
    return _clip(recency_component * 0.7 + channel_bonus)


def compute_financial_score(master_profile: Dict[str, Any], clv_reference: float = 5000.0) -> float:
    clv = master_profile.get("predictive_clv")
    if clv is None:
        clv = master_profile.get("historical_clv")
    clv = _to_float(clv)
    if clv_reference <= 0:
        return 0.0
    return _clip((clv / clv_reference) * 100.0)


_MEMBERSHIP_TIER_BASE = {"platinum": 100.0, "gold": 80.0, "silver": 60.0, "bronze": 40.0}


def compute_loyalty_score(master_profile: Dict[str, Any]) -> float:
    tier = (master_profile.get("membership_tier") or "").lower()
    base = _MEMBERSHIP_TIER_BASE.get(tier, 20.0)
    customer_since = master_profile.get("customer_since")
    tenure_days = (date.today() - customer_since).days if customer_since is not None else 0
    tenure_bonus = min(max(tenure_days, 0) / 365.0 * 20.0, 20.0)
    return _clip(base * 0.8 + tenure_bonus)


def compute_relationship_score(master_profile: Dict[str, Any]) -> float:
    channel_component = min(len(master_profile.get("source_systems") or []) * 20.0, 60.0)
    secondary_contacts = len(master_profile.get("secondary_emails") or []) + len(
        master_profile.get("secondary_phones") or []
    )
    contact_component = min(secondary_contacts * 10.0, 40.0)
    return _clip(channel_component + contact_component)


_RISK_SEGMENT_BONUS = {"low": 0.0, "medium": 15.0, "high": 30.0, "critical": 45.0}
_KYC_STATUS_BONUS = {"verified": 0.0, "pending": 10.0, "unverified": 20.0, "rejected": 40.0}


def compute_risk_score(master_profile: Dict[str, Any]) -> float:
    churn_probability = master_profile.get("churn_probability")
    base = _to_float(churn_probability) * 100.0 if churn_probability is not None else 20.0
    base += _RISK_SEGMENT_BONUS.get((master_profile.get("risk_segment") or "").lower(), 0.0)
    base += _KYC_STATUS_BONUS.get((master_profile.get("kyc_status") or "").lower(), 0.0)
    return _clip(base)


# Weights applied in compute_persona_score(). Positive component weights sum
# to 0.85; the remaining 0.15 is applied to (100 - risk_score) so a
# risk_score of 0 contributes its full 15 points and a risk_score of 100
# contributes 0 -- keeping the overall persona_score bounded to [0, 100].
_SCORE_WEIGHTS = {
    "behavior": 0.20,
    "engagement": 0.20,
    "financial": 0.20,
    "loyalty": 0.15,
    "relationship": 0.10,
    "risk": 0.15,
}


def compute_persona_score(scores: Dict[str, float]) -> float:
    positive_weighted = sum(
        scores[key] * _SCORE_WEIGHTS[key] for key in ("behavior", "engagement", "financial", "loyalty", "relationship")
    )
    risk_weighted = (100.0 - scores["risk"]) * _SCORE_WEIGHTS["risk"]
    return _clip(positive_weighted + risk_weighted)


def compute_customer_value_tier(scores: Dict[str, float]) -> str:
    value_index = (scores["financial"] + scores["loyalty"]) / 2.0
    if value_index >= 80:
        return "champion"
    if value_index >= 60:
        return "high_value"
    if value_index >= 35:
        return "growth_potential"
    return "standard"


def compute_risk_level(risk_score: float) -> str:
    if risk_score >= 75:
        return "critical"
    if risk_score >= 50:
        return "high"
    if risk_score >= 25:
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
        features=_build_features(master_profile),
    )


class PersonaResolutionEngine:
    """Computes and persists an explainable, versioned "customer persona" for
    an already-resolved master profile: turns identity *matching* output
    (``cdp_master_profiles``) into identity *understanding*
    (``cdp_customer_personas`` + ``cdp_persona_features`` +
    ``cdp_persona_score_details`` + ``cdp_persona_history``).

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
    HISTORY_SCORE_DELTA_THRESHOLD = 5.0

    def __init__(self, schema: str = "customer360"):
        self.schema = schema

    def _table(self, name: str) -> str:
        return f"{self.schema}.{name}" if self.schema else name

    def _fetch_master_profile(self, cursor, tenant_id, master_profile_id) -> Optional[Dict[str, Any]]:
        columns = ", ".join(MASTER_PROFILE_SELECT_COLUMNS)
        query = f"""
            SELECT {columns}
            FROM {self._table('cdp_master_profiles')}
            WHERE master_profile_id = %s AND tenant_id = %s;
        """
        cursor.execute(query, (master_profile_id, tenant_id))
        return cursor.fetchone()

    def _fetch_current_persona(self, cursor, tenant_id, master_profile_id) -> Optional[Dict[str, Any]]:
        query = f"""
            SELECT persona_id, persona_name, persona_score
            FROM {self._table('cdp_customer_personas')}
            WHERE tenant_id = %s AND master_profile_id = %s AND is_active = TRUE
            ORDER BY computed_at DESC
            LIMIT 1;
        """
        cursor.execute(query, (tenant_id, master_profile_id))
        return cursor.fetchone()

    def _next_computed_version(self, cursor, tenant_id, master_profile_id, persona_code) -> int:
        query = f"""
            SELECT COALESCE(MAX(computed_version), 0) + 1 AS next_version
            FROM {self._table('cdp_customer_personas')}
            WHERE tenant_id = %s AND master_profile_id = %s AND persona_code = %s;
        """
        cursor.execute(query, (tenant_id, master_profile_id, persona_code))
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
        self, cursor, tenant_id, domain, master_profile_id, computation: PersonaComputation, computed_version: int
    ) -> Any:
        query = f"""
            INSERT INTO {self._table('cdp_customer_personas')}
                (tenant_id, domain, master_profile_id, persona_code, persona_name,
                 persona_category, persona_summary, persona_score, confidence_score,
                 behavior_score, engagement_score, financial_score, loyalty_score,
                 relationship_score, risk_score, lifecycle_stage, customer_value_tier,
                 risk_level, next_best_action, llm_provider, llm_model,
                 computed_version, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, TRUE)
            RETURNING persona_id;
        """
        cursor.execute(
            query,
            (
                tenant_id,
                domain,
                master_profile_id,
                computation.persona_code,
                computation.persona_name,
                computation.persona_category,
                computation.persona_summary,
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
                computation.llm_provider,
                computation.llm_model,
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

    def resolve_persona(self, cursor, tenant_id, master_profile_id) -> Optional[Dict[str, Any]]:
        """Computes and persists a fresh persona snapshot for one master
        profile. Returns a small summary dict on success, or ``None`` if the
        profile could not be found OR anything failed along the way -- this
        method NEVER raises, so it is always safe to call from inside
        resolver.py's per-tenant transaction without risking a rollback of
        otherwise-successful identity matching work."""
        try:
            master_profile = self._fetch_master_profile(cursor, tenant_id, master_profile_id)
            if master_profile is None:
                return None

            computation = compute_persona(master_profile)
            old_persona = self._fetch_current_persona(cursor, tenant_id, master_profile_id)
            computed_version = self._next_computed_version(
                cursor, tenant_id, master_profile_id, computation.persona_code
            )

            self._deactivate_previous_personas(cursor, tenant_id, master_profile_id)
            persona_id = self._insert_persona(
                cursor, tenant_id, master_profile.get("domain") or "retail", master_profile_id, computation,
                computed_version,
            )
            self._insert_features(cursor, persona_id, computation.features)
            self._insert_score_details(cursor, persona_id, computation)

            score_delta = None
            name_changed = True
            if old_persona is not None:
                old_score = _to_float(old_persona.get("persona_score"))
                score_delta = abs(old_score - computation.persona_score)
                name_changed = old_persona.get("persona_name") != computation.persona_name
            if (
                old_persona is None
                or name_changed
                or (score_delta is not None and score_delta >= self.HISTORY_SCORE_DELTA_THRESHOLD)
            ):
                self._insert_history(cursor, persona_id, old_persona, computation)

            self._update_master_profile(cursor, tenant_id, master_profile_id, persona_id, computation)

            return {
                "persona_id": persona_id,
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
