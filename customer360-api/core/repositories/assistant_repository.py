"""Facts about the profile on screen, for the LEO Assistant.

Only a fixed allowlist of non-PII columns is ever read into the prompt, and a field is sent only
if the attribute catalog also says it is not PII (fail closed: a field missing from the catalog is
not sent). Names, emails, phones, addresses, ids, the free-form dicts and ``persona_summary`` (free
text that may echo a name) are never touched.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from leo_customer360_dao.models.identity import CdpCustomerPersona, CdpMasterProfile, CdpProfileAttribute

# (column on cdp_master_profiles, label shown to the model). Order is the order shown.
PROFILE_FIELDS = (
    ("lifecycle_stage", "Lifecycle stage"),
    ("churn_risk_tier", "Churn risk tier"),
    ("churn_probability", "Churn probability"),
    ("predictive_clv", "Predictive CLV"),
    ("historical_clv", "Historical CLV"),
    ("clv_segment", "CLV segment"),
    ("engagement_score", "Engagement score"),
    ("lead_grade", "Lead grade"),
    ("latest_nps_score", "Latest NPS score"),
    ("preferred_channel", "Preferred channel"),
    ("segmentation_tags", "Segmentation tags"),
    ("last_activity_at", "Last activity"),
    ("customer_since", "Customer since"),
    ("domain", "Domain"),
    ("persona_name", "Persona"),
)
# Computed persona fields: scores and labels from the persona engine, not catalog attributes.
PERSONA_FIELDS = (
    ("customer_value_tier", "Customer value tier"),
    ("risk_level", "Persona risk level"),
    ("persona_score", "Persona score"),
    ("match_score", "Persona match score"),
    ("confidence_score", "Persona confidence"),
    ("behavior_score", "Behavior score"),
    ("engagement_score", "Persona engagement score"),
    ("financial_score", "Financial score"),
    ("loyalty_score", "Loyalty score"),
    ("relationship_score", "Relationship score"),
    ("risk_score", "Risk score"),
    ("next_best_action", "Next best action"),
)
DESCRIPTION_MAX_CHARS = 160
MAX_TAGS = 10


def show_value(value) -> Optional[str]:
    """A short plain-text rendering of a value, or None when there is nothing to say."""
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, Decimal):
        return f"{value.quantize(Decimal('0.01')):f}"
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value[:MAX_TAGS])
    return str(value)


def build_profile_facts(profile, persona, catalog: dict[str, tuple[str, bool]]) -> list[str]:
    """Plain lines such as ``Churn risk tier (churn_risk_tier): high — <catalog description>``.

    ``catalog`` maps attribute code -> (description, is_pii). A profile field is included only
    when its catalog row exists and is not PII.
    """
    lines = []
    for column, label in PROFILE_FIELDS:
        entry = catalog.get(column)
        if entry is None or entry[1]:  # not in the catalog, or marked PII: do not send
            continue
        text = show_value(getattr(profile, column, None))
        if text is None:
            continue
        help_text = (entry[0] or "").strip()[:DESCRIPTION_MAX_CHARS]
        lines.append(f"{label} ({column}): {text}" + (f" — {help_text}" if help_text else ""))
    if persona is None:
        # Said explicitly: otherwise "what should I do next?" cannot tell "no persona yet" from "not shown".
        lines.append("Persona: not computed yet for this customer, so there is no Next Best Action")
    else:
        for column, label in PERSONA_FIELDS:
            text = show_value(getattr(persona, column, None))
            if text is not None:
                lines.append(f"{label}: {text}")
    return lines


class AssistantRepository:
    """Loads the allowlisted facts for one profile inside the caller's tenant."""

    def __init__(self, session: Session):
        self.session = session

    def profile_facts(self, tenant_id: uuid.UUID, master_profile_id: uuid.UUID) -> Optional[list[str]]:
        """Facts for the profile, or None when it does not exist in this tenant.

        Row-level security already hides other tenants' rows; the explicit tenant check is the
        second line of defence, as the project's guardrails require.
        """
        profile = self.session.get(CdpMasterProfile, master_profile_id)
        if profile is None or profile.tenant_id != tenant_id:
            return None
        persona = (
            self.session.get(CdpCustomerPersona, profile.current_persona_id)
            if profile.current_persona_id
            else None
        )
        if persona is not None and persona.tenant_id != tenant_id:
            persona = None
        codes = [column for column, _ in PROFILE_FIELDS]
        rows = self.session.execute(
            select(
                CdpProfileAttribute.attribute_internal_code,
                CdpProfileAttribute.description,
                CdpProfileAttribute.is_pii,
            ).where(CdpProfileAttribute.attribute_internal_code.in_(codes))
        ).all()
        catalog = {code: (description or "", bool(is_pii)) for code, description, is_pii in rows}
        return build_profile_facts(profile, persona, catalog)
