"""Segments/Audience Builder repository: segment rules execution, profile
matching, and membership computation.

Encapsulates:
- Executing segment SQL rules against cdp_master_profiles
- Counting matched profiles for pagination
- Fetching segmentable profile attributes for field picker

Uses the same synchronous SQLAlchemy Session as the rest of the API
(see core/database.py).
"""

import re
import uuid
from typing import Any, Optional

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, SmallInteger, Text, and_, func, or_, select, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Session

from leo_customer360_dao.config import settings
from leo_customer360_dao.crud.base import CRUDBase
from leo_customer360_dao.crud.segmentation import DOMAIN_ATTRIBUTES_JOIN_SQL, recompute_segment_membership
from leo_customer360_dao.models.identity import CdpMasterProfile, CdpProfileAttribute
from leo_customer360_dao.models.segmentation import CdpSegment
from leo_customer360_dao.schemas.identity import MasterProfileRead


_SEGMENTABLE_SOURCE_TABLES = ("cdp_master_profiles", "cdp_domain_profiles")
_MASTER_PROFILE_COLUMNS = frozenset(CdpMasterProfile.__table__.columns.keys())
_INTEGER_DATA_TYPES = {"SMALLINT", "INTEGER", "INT", "BIGINT", "SERIAL", "BIGSERIAL"}
_NUMERIC_DATA_TYPES = {"NUMERIC", "DECIMAL", "REAL", "FLOAT", "DOUBLE", "NUMBER"}
_DOMAIN_ATTRIBUTE_CASTS = {
    "BOOLEAN": "BOOLEAN",
    "BOOL": "BOOLEAN",
    "DATE": "DATE",
    "TIMESTAMP": "TIMESTAMP",
    "TIMESTAMPTZ": "TIMESTAMPTZ",
    "DATETIME": "TIMESTAMP",
    "UUID": "UUID",
}


class SegmentRepository:
    def __init__(self, session: Session):
        self.session = session
        self._crud = CRUDBase(CdpSegment)

    def get_segment(self, segment_id: uuid.UUID) -> Optional[CdpSegment]:
        """Get segment by ID."""
        return self._crud.get(self.session, segment_id)

    def get_matched_profiles(
        self, segment_id: uuid.UUID, validated_where_fragment: str, skip: int = 0, limit: int = 50
    ) -> list[dict]:
        """Runs the segment's validated SQL rules against cdp_master_profiles
        and returns matching active profiles.

        Args:
            segment_id: UUID of the segment
            validated_where_fragment: Pre-validated SQL WHERE fragment (from validate_sql_where_fragment)
            skip: Number of results to skip (pagination)
            limit: Maximum number of results to return

        Returns:
            List of matched master profile rows as dicts
        """
        segment = self.get_segment(segment_id)
        if segment is None:
            raise ValueError(f"CdpSegment '{segment_id}' not found")

        if not segment.sql_rules:
            return []

        stmt = text(
            f"""
            SELECT * FROM {settings.db_schema}.cdp_master_profiles
            {DOMAIN_ATTRIBUTES_JOIN_SQL.format(schema=settings.db_schema)}
            WHERE tenant_id = :tenant_id AND status_code = 1 AND ({validated_where_fragment})
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :skip
            """
        )
        rows = self.session.execute(
            stmt, {"tenant_id": str(segment.tenant_id), "limit": limit, "skip": skip}
        ).mappings().all()
        return [dict(row) for row in rows]

    def count_matched_profiles(self, segment_id: uuid.UUID, validated_where_fragment: str) -> int:
        """Count matched profiles for the segment.

        Args:
            segment_id: UUID of the segment
            validated_where_fragment: Pre-validated SQL WHERE fragment

        Returns:
            Count of matching profiles
        """
        segment = self.get_segment(segment_id)
        if segment is None:
            raise ValueError(f"CdpSegment '{segment_id}' not found")

        if not segment.sql_rules:
            return 0

        stmt = text(
            f"""
            SELECT count(*) FROM {settings.db_schema}.cdp_master_profiles
            {DOMAIN_ATTRIBUTES_JOIN_SQL.format(schema=settings.db_schema)}
            WHERE tenant_id = :tenant_id AND status_code = 1 AND ({validated_where_fragment})
            """
        )
        count = self.session.execute(stmt, {"tenant_id": str(segment.tenant_id)}).scalar_one()
        return count

    def recompute_membership(self, segment_id: uuid.UUID) -> dict:
        """Re-runs the segment's SQL rules, updating member_count/last_computed_at
        and syncing segment_tag into/out of cdp_master_profiles.segmentation_tags.

        Args:
            segment_id: UUID of the segment

        Returns:
            Dict with segment_id, member_count, last_computed_at
        """
        segment = self.get_segment(segment_id)
        if segment is None:
            raise ValueError(f"CdpSegment '{segment_id}' not found")

        if not segment.sql_rules:
            raise ValueError("Segment has no sql_rules to compute")

        recompute_segment_membership(self.session, segment)

        return {
            "segment_id": str(segment.segment_id),
            "member_count": segment.member_count,
            "last_computed_at": segment.last_computed_at,
        }

    def assign_audience_tags(
        self,
        segment_id: uuid.UUID,
        audience_tags: list[str],
        *,
        recompute_profiles: bool = True,
    ) -> CdpSegment:
        """Validate selected IAB ``tag_name`` values against ``cdp_audience_tags``,
        save them to ``cdp_segments.audience_tags``, update ``sql_rules`` /
        ``final_generated_sql``, and optionally recompute matching
        ``cdp_master_profiles`` rows."""
        from leo_customer360_dao.repositories.audience_tag_repository import AudienceTagRepository

        segment = self.get_segment(segment_id)
        if segment is None:
            raise ValueError(f"CdpSegment '{segment_id}' not found")

        tag_repo = AudienceTagRepository(self.session)
        validated_tags = tag_repo.validate_tag_names(audience_tags)
        updates: dict[str, Any] = {"audience_tags": validated_tags}
        if hasattr(self, "compose_sql_rules_with_audience_tags") and hasattr(self, "final_generated_sql"):
            composed = self.compose_sql_rules_with_audience_tags(segment.sql_rules, validated_tags)
            updates["sql_rules"] = composed
            updates["final_generated_sql"] = (
                self.final_generated_sql(composed, segment.tenant_id, validated_tags)
                if composed
                else None
            )
        segment = self._crud.update(self.session, segment, updates)
        if recompute_profiles and segment.sql_rules:
            recompute_segment_membership(self.session, segment)
        return segment

    # ------------------------------------------------------------------
    # Derived relative-date attributes & legacy rule conversion
    # ------------------------------------------------------------------
    DERIVED_ATTRIBUTES: dict[str, dict[str, Any]] = {
        "age_years": {
            "id": "age_years",
            "field": "date_of_birth",
            "name": "Age (years)",
            "description": "Calculated from Date of Birth each time the segment is recomputed.",
            "attribute_group": "DYNAMIC (RELATIVE TO TODAY)",
            "data_type": "INTEGER",
            "domain_scope": "all",
            "is_pii": False,
            "unit": "years",
            "source_type": "date",
            "min": 0,
            "max": 120,
        },
        "days_since_last_activity": {
            "id": "days_since_last_activity",
            "field": "last_activity_at",
            "name": "Days since last activity",
            "description": "Number of days elapsed since Last Activity At, calculated dynamically.",
            "attribute_group": "DYNAMIC (RELATIVE TO TODAY)",
            "data_type": "INTEGER",
            "domain_scope": "all",
            "is_pii": False,
            "unit": "days",
            "source_type": "timestamp",
            "min": 0,
            "max": 36500,
        },
        "days_as_customer": {
            "id": "days_as_customer",
            "field": "customer_since",
            "name": "Days as customer",
            "description": "Number of days since Customer Since date, calculated dynamically.",
            "attribute_group": "DYNAMIC (RELATIVE TO TODAY)",
            "data_type": "INTEGER",
            "domain_scope": "all",
            "is_pii": False,
            "unit": "days",
            "source_type": "date",
            "min": 0,
            "max": 36500,
        },
    }

    @classmethod
    def compile_derived_rule_sql(
        cls,
        rule_id: str,
        operator: str,
        value: Any,
        tz: str = "Asia/Ho_Chi_Minh",
    ) -> str:
        """Compile a derived relative-date rule into an index-friendly SQL predicate."""
        spec = cls.DERIVED_ATTRIBUTES.get(rule_id)
        if not spec:
            raise ValueError(f"Unknown derived attribute: {rule_id}")

        col = spec["field"]
        safe_tz = re.sub(r"[^A-Za-z0-9_+/\-]", "", tz or "Asia/Ho_Chi_Minh") or "Asia/Ho_Chi_Minh"
        today_expr = f"(now() AT TIME ZONE '{safe_tz}')::date"

        if operator == "is_null":
            return f"{col} IS NULL"
        if operator == "is_not_null":
            return f"{col} IS NOT NULL"

        def _parse_int(v: Any) -> int:
            if isinstance(v, bool):
                raise ValueError(f"Invalid integer value for {rule_id}: {v!r}")
            n = int(float(str(v).strip()))
            if n < 0:
                raise ValueError(f"Value for {rule_id} must be non-negative: {n}")
            return n

        if rule_id == "age_years":
            if operator in ("in", "not_in"):
                vals = value if isinstance(value, (list, tuple)) else [v.strip() for v in str(value or "").split(",")]
                vals = [v for v in vals if v is not None and str(v).strip() != ""]
                if not vals:
                    raise ValueError(f"Operator {operator} requires at least one value for {rule_id}")
                or_parts = [cls.compile_derived_rule_sql(rule_id, "equal", v, tz=tz) for v in vals]
                return f"({' OR '.join(or_parts)})" if operator == "in" else f"NOT ({' OR '.join(or_parts)})"
            if operator in ("between", "not_between"):
                if not isinstance(value, (list, tuple)) or len(value) < 2:
                    raise ValueError("Age 'between' requires two integer values.")
                a, b = _parse_int(value[0]), _parse_int(value[1])
                if a > b:
                    a, b = b, a
                if operator == "between":
                    return (
                        f"({col} <= ({today_expr} - INTERVAL '{a} years') "
                        f"AND {col} > ({today_expr} - INTERVAL '{b + 1} years'))"
                    )
                return (
                    f"({col} > ({today_expr} - INTERVAL '{a} years') "
                    f"OR {col} <= ({today_expr} - INTERVAL '{b + 1} years'))"
                )
            n = _parse_int(value[0] if isinstance(value, (list, tuple)) else value)
            if operator == "greater_or_equal":
                return f"{col} <= ({today_expr} - INTERVAL '{n} years')"
            if operator == "greater":
                return f"{col} <= ({today_expr} - INTERVAL '{n + 1} years')"
            if operator == "less":
                return f"{col} > ({today_expr} - INTERVAL '{n} years')"
            if operator == "less_or_equal":
                return f"{col} > ({today_expr} - INTERVAL '{n + 1} years')"
            if operator == "equal":
                return (
                    f"({col} <= ({today_expr} - INTERVAL '{n} years') "
                    f"AND {col} > ({today_expr} - INTERVAL '{n + 1} years'))"
                )
            if operator == "not_equal":
                return (
                    f"({col} > ({today_expr} - INTERVAL '{n} years') "
                    f"OR {col} <= ({today_expr} - INTERVAL '{n + 1} years'))"
                )
            raise ValueError(f"Unsupported operator {operator!r} for {rule_id}")

        base_expr = "now()" if spec["source_type"] == "timestamp" else today_expr
        if operator in ("in", "not_in"):
            vals = value if isinstance(value, (list, tuple)) else [v.strip() for v in str(value or "").split(",")]
            vals = [v for v in vals if v is not None and str(v).strip() != ""]
            if not vals:
                raise ValueError(f"Operator {operator} requires at least one value for {rule_id}")
            if spec["source_type"] == "date":
                date_exprs = [f"({today_expr} - INTERVAL '{_parse_int(v)} days')" for v in vals]
                return f"{col} {'IN' if operator == 'in' else 'NOT IN'} ({', '.join(date_exprs)})"
            or_parts = [cls.compile_derived_rule_sql(rule_id, "equal", v, tz=tz) for v in vals]
            return f"({' OR '.join(or_parts)})" if operator == "in" else f"NOT ({' OR '.join(or_parts)})"
        if operator in ("between", "not_between"):
            if not isinstance(value, (list, tuple)) or len(value) < 2:
                raise ValueError(f"{rule_id} 'between' requires two integer values.")
            a, b = _parse_int(value[0]), _parse_int(value[1])
            if a > b:
                a, b = b, a
            if operator == "between":
                return (
                    f"({col} <= ({base_expr} - INTERVAL '{a} days') "
                    f"AND {col} >= ({base_expr} - INTERVAL '{b} days'))"
                )
            return (
                f"({col} > ({base_expr} - INTERVAL '{a} days') "
                f"OR {col} < ({base_expr} - INTERVAL '{b} days'))"
            )

        n = _parse_int(value[0] if isinstance(value, (list, tuple)) else value)
        if operator == "greater":
            return f"{col} < ({base_expr} - INTERVAL '{n} days')"
        if operator == "greater_or_equal":
            return f"{col} <= ({base_expr} - INTERVAL '{n} days')"
        if operator == "less":
            return f"{col} > ({base_expr} - INTERVAL '{n} days')"
        if operator == "less_or_equal":
            return f"{col} >= ({base_expr} - INTERVAL '{n} days')"
        if operator == "equal":
            if spec["source_type"] == "date":
                return f"{col} = ({today_expr} - INTERVAL '{n} days')"
            return (
                f"({col} <= ({base_expr} - INTERVAL '{n} days') "
                f"AND {col} > ({base_expr} - INTERVAL '{n + 1} days'))"
            )
        if operator == "not_equal":
            if spec["source_type"] == "date":
                return f"{col} <> ({today_expr} - INTERVAL '{n} days')"
            return (
                f"({col} > ({base_expr} - INTERVAL '{n} days') "
                f"OR {col} <= ({base_expr} - INTERVAL '{n + 1} days'))"
            )
        raise ValueError(f"Unsupported operator {operator!r} for {rule_id}")

    @classmethod
    def compile_json_rules_sql(
        cls,
        json_rules: Optional[dict[str, Any]],
        tz: str = "Asia/Ho_Chi_Minh",
    ) -> Optional[str]:
        """Compile a QueryBuilder ``json_rules`` tree into a SQL WHERE fragment."""
        if not json_rules or not isinstance(json_rules, dict) or not json_rules.get("rules"):
            return None

        def _sql_quote(v: Any) -> str:
            return "'" + str("" if v is None else v).replace("'", "''") + "'"

        def _sql_scalar(v: Any, rule_type: Optional[str], field_name: str = "") -> str:
            if isinstance(v, bool):
                return "TRUE" if v else "FALSE"
            if isinstance(v, (int, float)):
                return str(v)
            rtype = str(rule_type or "").lower()
            if not rtype and field_name:
                if field_name.startswith("CAST(") and " AS INTEGER)" in field_name:
                    rtype = "integer"
                elif field_name.startswith("CAST(") and " AS NUMERIC)" in field_name:
                    rtype = "numeric"
                elif field_name.startswith("CAST(") and " AS BOOLEAN)" in field_name:
                    rtype = "boolean"
                else:
                    col = CdpMasterProfile.__table__.columns.get(field_name)
                    if col is not None:
                        if isinstance(col.type, Boolean):
                            rtype = "boolean"
                        elif isinstance(col.type, (SmallInteger, Integer)):
                            rtype = "integer"
                        elif isinstance(col.type, Numeric):
                            rtype = "numeric"
                        elif isinstance(col.type, JSONB):
                            rtype = "jsonb"
            if rtype == "boolean":
                token = str(v).strip().lower()
                return "TRUE" if token in ("true", "1", "yes") else "FALSE"
            if rtype in ("integer", "smallint", "bigint", "int"):
                return str(int(float(str(v).strip())))
            if rtype in ("double", "number", "numeric", "decimal", "float", "real"):
                return str(float(str(v).strip()))
            if rtype in ("json", "jsonb"):
                return _sql_quote(v) + ("::jsonb" if rtype == "jsonb" else "::json")
            return _sql_quote(v)

        def _compile_leaf(rule: dict[str, Any]) -> str:
            rule_id = str(rule.get("id") or rule.get("field") or "").strip()
            field = str(rule.get("field") or rule_id).strip()
            operator = str(rule.get("operator") or "equal").strip().lower()
            value = rule.get("value")

            if rule_id in cls.DERIVED_ATTRIBUTES:
                return cls.compile_derived_rule_sql(rule_id, operator, value, tz=tz)

            if not field or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$|^CAST\(dp\.domain_attributes->>'[A-Za-z0-9_]+' AS [A-Z]+\)$|^dp\.domain_attributes->>'[A-Za-z0-9_]+'$", field):
                raise ValueError(f"Unsupported field in json_rules: {field!r}")

            if field == "audience_tags":
                if operator in ("is_null", "is_empty"):
                    return f"({field} IS NULL OR cardinality({field}) = 0)"
                if operator in ("is_not_null", "is_not_empty"):
                    return f"({field} IS NOT NULL AND cardinality({field}) > 0)"
                vals = value if isinstance(value, (list, tuple)) else [value]
                cleaned = [_sql_quote(str(x).strip()) for x in vals if x is not None and str(x).strip()]
                if not cleaned:
                    raise ValueError("audience_tags rule requires at least one tag")
                arr_expr = f"ARRAY[{', '.join(cleaned)}]::text[]"
                if operator == "in":
                    return f"{field} && {arr_expr}"
                if operator == "equal":
                    return f"{field} @> {arr_expr}"
                if operator in ("not_in", "not_equal"):
                    return f"NOT (COALESCE({field}, ARRAY[]::text[]) && {arr_expr})"
                raise ValueError(f"Unsupported audience_tags operator: {operator!r}")

            if operator == "is_null":
                return f"{field} IS NULL"
            if operator == "is_not_null":
                return f"{field} IS NOT NULL"
            if operator == "is_empty":
                return f"{field} = ''"
            if operator == "is_not_empty":
                return f"{field} <> ''"

            rtype = rule.get("type")
            if operator in ("in", "not_in"):
                vals = value if isinstance(value, (list, tuple)) else [v.strip() for v in str(value or "").split(",")]
                vals = [v for v in vals if v is not None and str(v).strip() != ""]
                if not vals:
                    raise ValueError(f"Operator {operator} requires at least one value")
                joined = ", ".join(_sql_scalar(v, rtype, field) for v in vals)
                return f"{field} {'IN' if operator == 'in' else 'NOT IN'} ({joined})"

            if operator in ("between", "not_between"):
                if not isinstance(value, (list, tuple)) or len(value) < 2:
                    raise ValueError(f"Operator {operator} requires two values")
                v0 = _sql_scalar(value[0], rtype, field)
                v1 = _sql_scalar(value[1], rtype, field)
                return f"{field} {'BETWEEN' if operator == 'between' else 'NOT BETWEEN'} {v0} AND {v1}"

            scalar = value[0] if isinstance(value, (list, tuple)) and value else value
            op_map = {
                "equal": "=",
                "not_equal": "<>",
                "less": "<",
                "less_or_equal": "<=",
                "greater": ">",
                "greater_or_equal": ">=",
            }
            if operator in op_map:
                return f"{field} {op_map[operator]} {_sql_scalar(scalar, rtype, field)}"
            if operator == "begins_with":
                return f"{field} LIKE {_sql_quote(str(scalar) + '%')}"
            if operator == "contains":
                return f"{field} LIKE {_sql_quote('%' + str(scalar) + '%')}"
            if operator == "ends_with":
                return f"{field} LIKE {_sql_quote('%' + str(scalar))}"
            raise ValueError(f"Unsupported operator {operator!r}")

        def _compile_group(group: dict[str, Any], is_root: bool = False) -> str:
            cond = str(group.get("condition") or "AND").upper()
            if cond not in ("AND", "OR"):
                cond = "AND"
            parts: list[str] = []
            for item in group.get("rules") or []:
                if not isinstance(item, dict):
                    continue
                if "rules" in item:
                    sub = _compile_group(item, is_root=False)
                    if sub:
                        parts.append(sub)
                else:
                    parts.append(_compile_leaf(item))
            if not parts:
                return ""
            joined = f" {cond} ".join(parts)
            return joined if is_root and len(parts) == 1 else f"({joined})"

        compiled = _compile_group(json_rules, is_root=True)
        return compiled or None

    def get_segmentable_attributes(self, domain: Optional[str] = None) -> list[dict]:
        """Return the catalog of attributes that are valid to reference in a
        segment's SQL rules (Audience Builder field picker).

        Args:
            domain: Optional domain to filter attributes for

        Returns:
            List of attribute dicts with field, name, description, etc.
        """
        direct_master_profile_attribute = and_(
            CdpProfileAttribute.attribute_internal_code == CdpProfileAttribute.master_profile_column,
            CdpProfileAttribute.attribute_internal_code.in_(_MASTER_PROFILE_COLUMNS),
        )
        stmt = select(CdpProfileAttribute).where(
            CdpProfileAttribute.is_segmentable.is_(True),
            CdpProfileAttribute.status == "ACTIVE",
            or_(CdpProfileAttribute.source_table.in_(_SEGMENTABLE_SOURCE_TABLES), direct_master_profile_attribute),
        )
        if domain:
            stmt = stmt.where(CdpProfileAttribute.domain_scope.in_(["all", domain]))
        stmt = stmt.order_by(
            func.lower(CdpProfileAttribute.attribute_group),
            CdpProfileAttribute.display_order.asc().nullslast(),
            func.lower(CdpProfileAttribute.name),
            func.lower(CdpProfileAttribute.attribute_internal_code),
        )

        attributes = self.session.execute(stmt).scalars().all()

        def _master_profile_data_type(attribute: CdpProfileAttribute) -> str:
            field = attribute.master_profile_column or attribute.attribute_internal_code
            column = CdpMasterProfile.__table__.columns.get(field)
            if column is None or field != attribute.attribute_internal_code:
                return str(attribute.data_type or "TEXT").upper()

            column_type = column.type
            if isinstance(column_type, Boolean):
                return "BOOLEAN"
            if isinstance(column_type, SmallInteger):
                return "SMALLINT"
            if isinstance(column_type, Integer):
                return "INTEGER"
            if isinstance(column_type, Numeric):
                return "NUMERIC"
            if isinstance(column_type, Date) and not isinstance(column_type, DateTime):
                return "DATE"
            if isinstance(column_type, DateTime):
                return "TIMESTAMP"
            if isinstance(column_type, JSONB):
                return "JSONB"
            if isinstance(column_type, ARRAY):
                return "ARRAY"
            if isinstance(column_type, Text):
                return "TEXT"
            return str(attribute.data_type or "TEXT").upper()

        def _segmentable_field(attribute: CdpProfileAttribute, data_type: str) -> str:
            """SQL-safe field reference for the Audience Builder field picker."""
            if getattr(attribute, "source_table", None) == "cdp_domain_profiles":
                key = str(attribute.attribute_internal_code).replace("'", "''")
                field = f"dp.domain_attributes->>'{key}'"
                cast_type = _DOMAIN_ATTRIBUTE_CASTS.get(data_type)
                if cast_type or data_type in _INTEGER_DATA_TYPES or data_type in _NUMERIC_DATA_TYPES:
                    return f"CAST({field} AS {cast_type or ('INTEGER' if data_type in _INTEGER_DATA_TYPES else 'NUMERIC')})"
                return field
            return attribute.master_profile_column or attribute.attribute_internal_code

        result = []
        present_fields: set[str] = set()
        for attribute in attributes:
            data_type = _master_profile_data_type(attribute)
            field_ref = _segmentable_field(attribute, data_type)
            present_fields.add(field_ref)
            result.append(
                {
                    "field": field_ref,
                    "name": attribute.name,
                    "description": attribute.description,
                    "attribute_group": attribute.attribute_group,
                    "data_type": data_type,
                    "domain_scope": attribute.domain_scope,
                    "is_pii": attribute.is_pii,
                }
            )
        for derived_id, spec in self.DERIVED_ATTRIBUTES.items():
            if spec["field"] in present_fields:
                result.append(
                    {
                        "id": derived_id,
                        "field": spec["field"],
                        "name": spec["name"],
                        "description": spec["description"],
                        "attribute_group": spec["attribute_group"],
                        "data_type": spec["data_type"],
                        "domain_scope": spec["domain_scope"],
                        "is_pii": spec["is_pii"],
                    }
                )
        return result
