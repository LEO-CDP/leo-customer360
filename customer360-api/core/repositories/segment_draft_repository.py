"""Description -> agent -> rule_validator -> SegmentDraftResult. Writes nothing, makes no SQL.

The form's save path compiles the rules, as for a hand-built segment. Tenant comes from the
router (request.state). The catalog (`cdp_profile_attributes`) is shared, not under RLS.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from typing import Any, Iterable, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from leo_customer360_agent.client import (
    AIProviderError,
    SegmentRulesBrief,
    generate_segment_rules,
)
from leo_customer360_dao.config import settings
from leo_customer360_dao.repositories.segment_respository import SegmentRepository
from leo_customer360_dao.schemas.segmentation import SegmentDraftResult
from leo_customer360_dao.utils.rule_validator import (
    RuleValidationError,
    UnresolvedValue,
    form_hints,
    looks_like_sql,
    validate_rules,
)

# Catalog keys the model may see; the rest are platform internals.
_MODEL_VISIBLE_KEYS = ("field", "name", "description", "data_type")

DESCRIPTION_MAX_LEN = 2000
SEGMENT_TAG_MAX_LEN = 64

# pg_get_constraintdef() of `CHECK (col IN ('a','b'))`:
#   CHECK ((col = ANY (ARRAY['a'::text, 'b'::text])))
# Other CHECK shapes are ignored.
_CHECK_IN_LIST = re.compile(r"^CHECK \(\((\w+) = ANY \(\(?ARRAY\[(.*)\]\)?(?:::\w+\[\])?\)\)\)$")
_ARRAY_ITEM = re.compile(r"'((?:[^']|'')*)'::[\w ]+")

# Text columns with at most this many distinct values get their real values for case fixing.
MAX_KNOWN_VALUES = 20


class SegmentDraftValidationError(ValueError):
    """Refused before any model call."""


class SegmentDraftGenerationError(RuntimeError):
    """Agent unreachable or reply is not a rule tree."""


def parse_allowed_values(constraint_defs: Iterable[str]) -> dict[str, list[str]]:
    """Column -> allowed values, from ``pg_get_constraintdef`` output."""
    allowed: dict[str, list[str]] = {}
    for definition in constraint_defs:
        match = _CHECK_IN_LIST.match(str(definition or "").strip())
        if not match:
            continue
        values = [v.replace("''", "'") for v in _ARRAY_ITEM.findall(match.group(2))]
        if values:
            allowed[match.group(1)] = values
    return allowed


def _normalize_state_text(value: Optional[str]) -> Optional[str]:
    """Collapse whitespace and cap untrusted browser state; blank -> ``None``."""
    text = " ".join(str(value or "").split())
    return text[:DESCRIPTION_MAX_LEN] or None


# English and Vietnamese for the API's own messages.
_MESSAGES = {
    "en": {
        "sql": "This looks like SQL, which segments do not accept. Please describe the audience in plain words.",
        "not_possible": "This request cannot be expressed with profile attributes.",
        "ask": "Which customers should this segment include?",
    },
    "vi": {
        "sql": "Nội dung này trông giống SQL, phân khúc không chấp nhận SQL. Vui lòng mô tả đối tượng bằng lời thường.",
        "not_possible": "Yêu cầu này không thể biểu diễn bằng thuộc tính hồ sơ khách hàng.",
        "ask": "Phân khúc này nên gồm những khách hàng nào?",
    },
}


def _lang(text: str) -> str:
    """Return "vi" when the text has Vietnamese accents or đ, else "en".

    ponytail: accent-free Vietnamese reads as English; use a language detector if that matters.
    """
    has_accents = any(unicodedata.combining(c) for c in unicodedata.normalize("NFD", text))
    return "vi" if has_accents or "đ" in text.lower() else "en"


def _segment_tag(raw: str, fallback: str) -> str:
    tag = re.sub(r"[^a-z0-9]+", "_", (raw or fallback or "").lower()).strip("_")
    return tag[:SEGMENT_TAG_MAX_LEN].rstrip("_") or "ai_segment"


def _model_rules(tree: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The tree reduced to field/operator/value for the model."""
    if not tree:
        return None

    def walk(node: dict[str, Any]) -> dict[str, Any]:
        if "rules" in node:
            return {"condition": node["condition"], "rules": [walk(c) for c in node["rules"]]}
        return {"field": node["field"], "operator": node["operator"], "value": node.get("value")}

    return walk(tree)


class SegmentDraftRepository:
    """Produce segment drafts from natural language. Read-only."""

    def __init__(self, session: Session):
        self.session = session

    # ----------------------------------------------------------------- catalog
    def load_allowed_values(self) -> dict[str, list[str]]:
        """Allowed values of ``cdp_master_profiles`` columns, from their CHECK constraints."""
        rows = self.session.execute(
            text(
                """
                SELECT pg_get_constraintdef(c.oid)
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE c.contype = 'c' AND n.nspname = :schema AND t.relname = 'cdp_master_profiles'
                """
            ),
            {"schema": settings.db_schema},
        ).scalars().all()
        return parse_allowed_values(rows)

    def load_known_values(self, tenant_id: uuid.UUID, catalog: list[dict[str, Any]]) -> None:
        """Attach the tenant's distinct values to low-cardinality, non-PII text columns.

        Validator-only (case fixing); never in :meth:`model_view`.
        """
        # Only real text columns: a catalog field that isn't one would abort the transaction.
        text_columns = set(self.session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = 'cdp_master_profiles' AND data_type = 'text'"
            ),
            {"schema": settings.db_schema},
        ).scalars().all())
        # ponytail: one DISTINCT scan of the tenant's profiles per column; cache per tenant if slow.
        for row in catalog:
            column = str(row.get("field") or "")
            if column not in text_columns or row.get("allowed_values") or row.get("is_pii") is not False:
                continue
            values = self.session.execute(
                text(
                    f'SELECT DISTINCT "{column}" FROM {settings.db_schema}.cdp_master_profiles '
                    f'WHERE tenant_id = :tenant_id AND "{column}" IS NOT NULL LIMIT :limit'
                ),
                {"tenant_id": str(tenant_id), "limit": MAX_KNOWN_VALUES + 1},
            ).scalars().all()
            if 0 < len(values) <= MAX_KNOWN_VALUES:
                row["known_values"] = [str(v) for v in values]

    def load_catalog(self, domain: str, tenant_id: Optional[uuid.UUID] = None) -> list[dict[str, Any]]:
        """Segmentable attributes the form offers, with allowed (and, given a tenant, known) values."""
        catalog = [dict(row) for row in SegmentRepository(self.session).get_segmentable_attributes(domain=domain)]
        allowed = self.load_allowed_values()
        for row in catalog:
            values = allowed.get(str(row.get("field") or ""))
            if values:
                row["allowed_values"] = values
        catalog = [row for row in catalog if form_hints(row) is not None]
        if tenant_id is not None:
            self.load_known_values(tenant_id, catalog)
        return catalog

    @staticmethod
    def model_view(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Per attribute: visible keys plus form hints. Expects :meth:`load_catalog` rows."""
        return [{**{k: row.get(k) for k in _MODEL_VISIBLE_KEYS}, **form_hints(row)} for row in catalog]

    # ----------------------------------------------------------------- draft
    def draft_from_description(
        self,
        tenant_id: uuid.UUID,
        description: str,
        domain: str = "all",
        current_rules: Optional[dict[str, Any]] = None,
        so_far: Optional[str] = None,
        last_question: Optional[str] = None,
    ) -> SegmentDraftResult:
        """Description -> validated rule tree. Saves nothing.

        Multi-turn state from the browser (untrusted): ``so_far``, ``last_question``,
        ``current_rules`` (dropped if invalid). Returned ``so_far``: the model's on valid or a
        model question (else the incoming one); the incoming one on any refusal.

        ``tenant_id`` must come from auth. Bad input is a ``needs_clarification``/``rejected``
        result; raises only :class:`SegmentDraftValidationError` (before the model) and
        :class:`SegmentDraftGenerationError` (agent failure).
        """
        if not isinstance(tenant_id, uuid.UUID):
            raise SegmentDraftValidationError("tenant_id must come from the authenticated request")
        description = (description or "").strip()
        if not description:
            raise SegmentDraftValidationError("Describe the audience you want to build")
        if len(description) > DESCRIPTION_MAX_LEN:
            raise SegmentDraftValidationError(
                f"Description is too long ({len(description)} > {DESCRIPTION_MAX_LEN} characters)"
            )
        domain = (domain or "all").strip() or "all"
        so_far = _normalize_state_text(so_far)
        last_question = _normalize_state_text(last_question)
        lang = _lang(description)
        messages = _MESSAGES[lang]

        # Before the model, which would silently drop the SQL half. State is checked too.
        if any(looks_like_sql(t) for t in (description, so_far, last_question) if t):
            return SegmentDraftResult(
                validation_status="rejected",
                ready_for_segment_persistence=False,
                domain=domain,
                question=messages["sql"],
                so_far=so_far,
            )

        catalog = self.load_catalog(domain, tenant_id)
        if not catalog:
            raise SegmentDraftValidationError(
                "No segmentable attributes are configured for this domain, so no rule can be built"
            )
        # Don't hold a transaction open across the slow model call.
        self.session.commit()

        # Untrusted: only a valid tree reaches the model.
        builder_rules = None
        if current_rules:
            try:
                builder_rules = validate_rules(current_rules, catalog).json_rules or None
            except RuleValidationError:
                builder_rules = None

        try:
            generated = generate_segment_rules(
                SegmentRulesBrief(
                    description=description,
                    attributes=self.model_view(catalog),
                    domain=domain,
                    current_rules=_model_rules(builder_rules),
                    so_far=so_far,
                    last_question=last_question,
                )
            )
        except AIProviderError as exc:
            raise SegmentDraftGenerationError(str(exc)) from exc

        base = {"interpretation": generated.explanation, "domain": domain}

        if not generated.json_rules.get("rules"):
            # Model declined; its explanation is the reason or the question.
            if generated.outcome == "not_possible":
                return SegmentDraftResult(
                    validation_status="rejected",
                    ready_for_segment_persistence=False,
                    question=generated.explanation or messages["not_possible"],
                    so_far=so_far,
                    **base,
                )
            return SegmentDraftResult(
                validation_status="needs_clarification",
                ready_for_segment_persistence=False,
                question=generated.explanation or messages["ask"],
                so_far=generated.so_far or so_far,
                **base,
            )

        try:
            validated = validate_rules(generated.json_rules, catalog)
        except UnresolvedValue as exc:
            return SegmentDraftResult(
                validation_status="needs_clarification",
                ready_for_segment_persistence=False,
                question=exc.question_in(lang),
                field=exc.field,
                suggestions=exc.suggestions,
                so_far=so_far,
                **base,
            )
        except RuleValidationError as exc:
            return SegmentDraftResult(
                validation_status="rejected",
                ready_for_segment_persistence=False,
                question=exc.question_in(lang),
                field=exc.field,
                suggestions=exc.suggestions,
                so_far=so_far,
                **base,
            )

        return SegmentDraftResult(
            validation_status="valid",
            ready_for_segment_persistence=True,
            json_rules=validated.json_rules,
            segment_tag=_segment_tag(generated.segment_tag, generated.segment_name),
            segment_name=generated.segment_name[:200],
            fields_used=validated.fields_used,
            so_far=generated.so_far or so_far,
            **base,
        )
