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

    @classmethod
    def final_generated_sql(cls, sql_rules: str, tenant_id: uuid.UUID) -> str:
        """Build the persisted tenant-scoped SQL representation for a segment."""
        where_clause = sql_rules.strip() if cls._has_wrapping_parentheses(sql_rules) else f"({sql_rules.strip()})"
        return (
            f"SELECT master_profile_id FROM {settings.db_schema}.cdp_master_profiles "
            f"WHERE tenant_id = '{tenant_id}'::uuid AND {where_clause}"
        )

    @classmethod
    def transform_create(cls, payload: dict[str, Any]) -> dict[str, Any]:
        """Normalize create rules and calculate their generated SQL."""
        sql_rules = payload.get("sql_rules")
        if sql_rules:
            payload["sql_rules"] = cls.normalize_relative_intervals(sql_rules)
            payload["final_generated_sql"] = cls.final_generated_sql(payload["sql_rules"], payload["tenant_id"])
        return payload

    @classmethod
    def transform_update(cls, segment: CdpSegment, payload: dict[str, Any]) -> dict[str, Any]:
        """Normalize changed rules and keep generated SQL synchronized."""
        if "sql_rules" in payload:
            if payload["sql_rules"]:
                payload["sql_rules"] = cls.normalize_relative_intervals(payload["sql_rules"])
                payload["final_generated_sql"] = cls.final_generated_sql(payload["sql_rules"], segment.tenant_id)
            else:
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