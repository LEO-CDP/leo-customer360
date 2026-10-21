"""Segment persistence, normalization, matching, and orchestration helpers."""

import logging
import re
import uuid
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError

from core.init_core_data import list_tenant_ids, seed_default_segments_with_breakdown
from leo_customer360_dao.crud.base import CRUDBase
from leo_customer360_dao.repositories.segment_respository import SegmentRepository as DaoSegmentRepository
from leo_customer360_dao.models.segmentation import CdpSegment
from core.utils.dagster_client import DagsterJobTriggerError, dagster_client
from leo_customer360_dao.config import settings

logger = logging.getLogger(__name__)

_RELATIVE_INTERVAL_PATTERN = re.compile(
    r"(?P<quote>['\"])(?P<sign>[+-])\s*(?P<amount>\d+)\s+"
    r"(?P<unit>milliseconds?|seconds?|minutes?|hours?|days?|weeks?|months?|years?)"
    r"(?P=quote)", re.IGNORECASE,
)


class SegmentRepository(DaoSegmentRepository):
    """Extends the DAO segment repository with API write and job helpers."""

    def __init__(self, session):
        """Create a repository bound to one SQLAlchemy session."""
        super().__init__(session)
        self._crud = CRUDBase(CdpSegment)

    @property
    def crud(self):
        """Expose the CRUD adapter to the generic router factory."""
        return self._crud

    @staticmethod
    def normalize_relative_intervals(sql_rules: str) -> str:
        """Translate quoted UI date offsets into PostgreSQL interval expressions."""
        def replace(match: re.Match[str]) -> str:
            sign = "+" if match.group("sign") == "+" else "-"
            return f"(now() {sign} INTERVAL '{match.group('amount')} {match.group('unit').lower()}')"

        return _RELATIVE_INTERVAL_PATTERN.sub(replace, sql_rules)

    @staticmethod
    def _has_wrapping_parentheses(value: str) -> bool:
        """Return whether parentheses wrap the complete SQL fragment."""
        stripped = value.strip()
        if not stripped.startswith("(") or not stripped.endswith(")"):
            return False
        depth = 0
        quote = None
        index = 0
        while index < len(stripped):
            char = stripped[index]
            if quote:
                if char == quote:
                    if index + 1 < len(stripped) and stripped[index + 1] == quote:
                        index += 2
                        continue
                    quote = None
            elif char in {"'", '"'}:
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0 and index != len(stripped) - 1:
                    return False
            index += 1
        return depth == 0 and quote is None

    @staticmethod
    def _audience_tags_sql_condition(audience_tags: list[str]) -> Optional[str]:
        """Build the PostgreSQL array overlap condition for ``audience_tags``."""
        cleaned = [
            tag.strip()
            for tag in (audience_tags or [])
            if isinstance(tag, str) and tag.strip()
        ]
        if not cleaned:
            return None
        quoted = ", ".join("'" + tag.replace("'", "''") + "'" for tag in cleaned)
        return f"audience_tags && ARRAY[{quoted}]::text[]"

    @staticmethod
    def _strip_auto_audience_tags_condition(sql_rules: str) -> str:
        """Remove any previously auto-appended ``audience_tags && ARRAY[...]::text[]`` suffix."""
        pattern = re.compile(
            r"^\((?P<base>.+)\)\s+AND\s+\(audience_tags\s+&&\s+ARRAY\[[^\]]*\]::text\[\]\)$",
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.match(sql_rules.strip())
        if match:
            return match.group("base").strip()
        solo_pattern = re.compile(
            r"^\(?audience_tags\s+&&\s+ARRAY\[[^\]]*\]::text\[\]\)?$",
            re.IGNORECASE | re.DOTALL,
        )
        if solo_pattern.match(sql_rules.strip()):
            return ""
        return sql_rules.strip()

    @classmethod
    def compose_sql_rules_with_audience_tags(
        cls,
        sql_rules: Optional[str],
        audience_tags: Optional[list[str]],
    ) -> Optional[str]:
        """Ensure selected ``audience_tags`` are included in ``sql_rules``."""
        base_sql = cls._strip_auto_audience_tags_condition(sql_rules or "") if sql_rules else ""
        tags_cond = cls._audience_tags_sql_condition(audience_tags or [])
        if not base_sql and not tags_cond:
            return sql_rules
        if not tags_cond:
            return base_sql or None
        if not base_sql:
            return f"({tags_cond})"
        if re.search(r"\baudience_tags\b", base_sql, re.IGNORECASE):
            return base_sql
        wrapped_base = base_sql if cls._has_wrapping_parentheses(base_sql) else f"({base_sql})"
        return f"{wrapped_base} AND ({tags_cond})"

    @classmethod
    def final_generated_sql(
        cls,
        sql_rules: str,
        tenant_id: uuid.UUID,
        audience_tags: Optional[list[str]] = None,
    ) -> str:
        """Build the persisted tenant-scoped SQL representation for a segment."""
        effective_rules = cls.compose_sql_rules_with_audience_tags(sql_rules, audience_tags) or sql_rules
        where_clause = (
            effective_rules.strip()
            if cls._has_wrapping_parentheses(effective_rules)
            else f"({effective_rules.strip()})"
        )
        return (
            f"SELECT master_profile_id FROM {settings.db_schema}.cdp_master_profiles "
            f"WHERE tenant_id = '{tenant_id}'::uuid AND {where_clause}"
        )

    @classmethod
    def transform_create(cls, payload: dict[str, Any]) -> dict[str, Any]:
        """Normalize create rules and calculate their generated SQL."""
        audience_tags: list[str] = []
        if "audience_tags" in payload and payload["audience_tags"] is not None:
            audience_tags = [
                tag.strip() for tag in payload["audience_tags"] if isinstance(tag, str) and tag.strip()
            ]
            payload["audience_tags"] = audience_tags
        sql_rules = payload.get("sql_rules")
        if sql_rules:
            normalized = cls.normalize_relative_intervals(sql_rules)
            composed = cls.compose_sql_rules_with_audience_tags(normalized, audience_tags) or normalized
            payload["sql_rules"] = composed
            payload["final_generated_sql"] = cls.final_generated_sql(
                composed, payload["tenant_id"], audience_tags
            )
        elif audience_tags:
            composed = cls.compose_sql_rules_with_audience_tags("", audience_tags)
            if composed:
                payload["sql_rules"] = composed
                payload["final_generated_sql"] = cls.final_generated_sql(
                    composed, payload["tenant_id"], audience_tags
                )
        return payload

    @classmethod
    def transform_update(cls, segment: CdpSegment, payload: dict[str, Any]) -> dict[str, Any]:
        """Normalize changed rules and keep generated SQL synchronized."""
        if "audience_tags" in payload and payload["audience_tags"] is not None:
            payload["audience_tags"] = [
                tag.strip() for tag in payload["audience_tags"] if isinstance(tag, str) and tag.strip()
            ]
        effective_tags = (
            payload["audience_tags"]
            if "audience_tags" in payload and payload["audience_tags"] is not None
            else list(getattr(segment, "audience_tags", None) or [])
        )
        if "sql_rules" in payload or "audience_tags" in payload:
            raw_sql = payload["sql_rules"] if "sql_rules" in payload else getattr(segment, "sql_rules", None)
            if raw_sql:
                normalized = cls.normalize_relative_intervals(raw_sql)
                composed = cls.compose_sql_rules_with_audience_tags(normalized, effective_tags)
                payload["sql_rules"] = composed
                payload["final_generated_sql"] = (
                    cls.final_generated_sql(composed, segment.tenant_id, effective_tags)
                    if composed
                    else None
                )
            elif effective_tags:
                composed = cls.compose_sql_rules_with_audience_tags("", effective_tags)
                payload["sql_rules"] = composed
                payload["final_generated_sql"] = (
                    cls.final_generated_sql(composed, segment.tenant_id, effective_tags)
                    if composed
                    else None
                )
            else:
                payload["sql_rules"] = None
                payload["final_generated_sql"] = None
        return payload

    @staticmethod
    def integrity_error_detail(exc: IntegrityError) -> Optional[str]:
        """Map the known segment uniqueness constraint to a client-safe message."""
        diagnostic = getattr(getattr(exc, "orig", None), "diag", None)
        if getattr(diagnostic, "constraint_name", None) == "uq_cdp_segments_tenant_tag":
            return "A segment with this tag already exists in this workspace."
        return None

    @staticmethod
    def trigger_recompute(segment: CdpSegment, trigger_reason: str) -> None:
        """Submit a best-effort tenant-scoped segment recompute job."""
        try:
            run_id = getattr(dagster_client.segmentation, trigger_reason)(
                tenant_id=str(segment.tenant_id), segment_id=str(segment.segment_id)
            )
            logger.info("Submitted segment recompute segment_id=%s run_id=%s", segment.segment_id, run_id)
        except DagsterJobTriggerError:
            logger.warning("Could not submit segment recompute segment_id=%s", segment.segment_id, exc_info=True)

    @staticmethod
    def resolve_seed_target_tenants(session, tenant_id: Optional[uuid.UUID], all_tenants: bool) -> list[uuid.UUID]:
        """Resolve explicit or all-tenant seed targets from the database."""
        if all_tenants:
            return list_tenant_ids(session)
        return [tenant_id] if tenant_id is not None else []

    @staticmethod
    def seed_defaults(session, tenant_ids: list[uuid.UUID]):
        """Seed default segments and return total and per-tenant counts."""
        return seed_default_segments_with_breakdown(session, tenant_ids=tenant_ids)