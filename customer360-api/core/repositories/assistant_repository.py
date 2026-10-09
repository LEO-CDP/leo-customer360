"""Facts about the profile on screen, for the LEO Assistant.

Only a fixed allowlist of non-PII columns is ever read into the prompt, and a field is sent only
if the attribute catalog also says it is not PII (fail closed: a field missing from the catalog is
not sent). Names, emails, phones, addresses, ids, the free-form dicts and ``persona_summary`` (free
text that may echo a name) are never touched.
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from leo_customer360_dao.models.identity import CdpCustomerPersona, CdpMasterProfile, CdpProfileAttribute
from leo_customer360_dao.models.system import SysAssistantMessage

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

WINDOW_MESSAGES = 6          # short-term memory: what the model sees of the chat so far
WINDOW_TEXT_MAX_CHARS = 1500  # each message is cut to this before it goes to the docs service, which keeps
                              # the last 2 at this length with a summary, else 6 cut to 600
RESTORE_MESSAGES = 50        # long-term memory: how much of a saved chat the panel gets back
RETENTION_DAYS = 30


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


class ConversationRepository:
    """LEO Assistant chat messages. Every query is limited to the caller's tenant AND user (a chat is
    readable only by its author), within the 30-day retention window; row-level security on the
    tenant is the second line of defence."""

    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _cutoff() -> datetime:
        return datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)

    @staticmethod
    def _same(column, value):
        return column.is_(None) if value is None else column == value

    def window(
        self,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        conversation_id: Optional[uuid.UUID],
        page: Optional[str],
        master_profile_id: Optional[uuid.UUID],
    ) -> tuple[uuid.UUID, list[dict], Optional[str]]:
        """(conversation id to use, recent messages oldest first, the chat's latest summary or None).

        An id that has no rows for this user (unknown, expired, someone else's) or whose chat started
        on another page or customer is not continued: a new id and no history. The three cases look
        the same, so the reply never reveals whether an id exists."""
        if conversation_id is not None:
            rows = list(
                self.session.scalars(
                    select(SysAssistantMessage)
                    .where(
                        SysAssistantMessage.tenant_id == tenant_id,
                        SysAssistantMessage.user_id == user_id,
                        SysAssistantMessage.conversation_id == conversation_id,
                        SysAssistantMessage.created_at > self._cutoff(),
                    )
                    .order_by(SysAssistantMessage.created_at.desc())
                    .limit(WINDOW_MESSAGES)
                )
            )
            if rows and rows[0].page == page and rows[0].master_profile_id == master_profile_id:
                return conversation_id, [
                    {
                        "role": row.role,
                        "text": row.message_text[:WINDOW_TEXT_MAX_CHARS],
                        "clarify": row.clarify is not None,
                    }
                    for row in reversed(rows)
                ], next((row.summary for row in rows if row.role == "assistant"), None)
        return uuid.uuid4(), [], None

    def add_exchange(
        self,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        page: Optional[str],
        master_profile_id: Optional[uuid.UUID],
        question: str,
        answer: str,
        status: str,
        clarify: Optional[str],
        sources: list[dict],
        summary: Optional[str] = None,
    ) -> None:
        """Store the (already masked) question and its answer, then drop this tenant's expired rows.
        The caller commits, so this lands in the same transaction as the audit row."""
        common = dict(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            page=page,
            master_profile_id=master_profile_id,
        )
        self.session.add(SysAssistantMessage(role="user", message_text=question, **common))
        self.session.flush()  # keep the question before its answer
        self.session.add(
            SysAssistantMessage(
                role="assistant", message_text=answer, status=status, clarify=clarify, sources=sources,
                summary=summary, **common
            )
        )
        self.session.execute(
            delete(SysAssistantMessage).where(
                SysAssistantMessage.tenant_id == tenant_id, SysAssistantMessage.created_at < self._cutoff()
            )
        )

    def latest(
        self,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        page: Optional[str],
        master_profile_id: Optional[uuid.UUID],
    ) -> tuple[Optional[uuid.UUID], list[SysAssistantMessage]]:
        """This user's most recent chat on this page and customer, newest messages up to the limit."""
        scope = (
            SysAssistantMessage.tenant_id == tenant_id,
            SysAssistantMessage.user_id == user_id,
            self._same(SysAssistantMessage.page, page),
            self._same(SysAssistantMessage.master_profile_id, master_profile_id),
            SysAssistantMessage.created_at > self._cutoff(),
        )
        conversation_id = self.session.scalar(
            select(SysAssistantMessage.conversation_id)
            .where(*scope)
            .order_by(SysAssistantMessage.created_at.desc())
            .limit(1)
        )
        if conversation_id is None:
            return None, []
        rows = list(
            self.session.scalars(
                select(SysAssistantMessage)
                .where(*scope, SysAssistantMessage.conversation_id == conversation_id)
                .order_by(SysAssistantMessage.created_at.desc())
                .limit(RESTORE_MESSAGES)
            )
        )
        return conversation_id, list(reversed(rows))
