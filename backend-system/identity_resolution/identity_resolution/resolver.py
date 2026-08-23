"""Core Customer Identity Resolution (CIR) logic.

This is the Python OOP replacement for the PostgreSQL stored procedure
``resolve_customer_identities_dynamic`` described in
``core-customer360/identity-resolution.md``, wired up against the real,
multi-tenant ``customer360`` schema defined in
``core-customer360/database-schema.sql`` (``cdp_raw_profiles_stage``,
``cdp_master_profiles``, ``cdp_profile_links``), which stores profile data
ingested from AppsFlyer, MoEngage and Web Tracking for retail, banking,
real_estate, travel, media, and education domains.

Matching rules are read dynamically from the ``cdp_profile_attributes``
metadata table (not part of ``database-schema.sql`` -- created on demand, see
``scripts/init_sample_data.py``).
"""

import logging
import re
from typing import Any, Dict, List, Optional, Sequence

from psycopg2.extras import Json, RealDictCursor

from .models import IdentityRule
from .persona import generate_persona_name, profile_looks_hashed
from .persona_engine import PersonaResolutionEngine
from .rls import set_tenant_context

logger = logging.getLogger(__name__)

# Scalar columns present with the same name/type on both cdp_raw_profiles_stage
# and cdp_master_profiles, merged with simple COALESCE semantics. Merging here
# is independent of matching: full_name is deliberately NOT an active
# is_identity_resolution rule (see init-core-database.sql) since common/shared
# names are an unreliable/false-positive-prone match signal on their own.
SCALAR_MERGE_FIELDS = ("full_name", "email", "phone_number")

# Base cdp_master_profiles columns needed to evaluate merge precedence;
# extended dynamically with any extra verified_field/timestamp_field named by
# active consolidation_config (see _collect_master_state_columns).
BASE_MASTER_STATE_COLUMNS = (
    "full_name",
    "email",
    "phone_number",
    "source_systems",
    "updated_at",
)

# Domain-scoped attributes moved from cdp_master_profiles to
# cdp_domain_profiles.domain_attributes.
DOMAIN_ATTRIBUTE_FIELDS = {
    "national_id",
    "kyc_status",
}

# Whitelist for column names pulled from consolidation_config (verified_field/
# timestamp_field) before they are interpolated into a dynamic SELECT --
# guards against a malformed/malicious cdp_profile_attributes row turning
# into SQL injection via consolidation_config.
_SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# Device/marketing identifiers stored as a single column on the raw stage
# table, but consolidated into a TEXT[] identity list on the master profile
# (one raw profile only ever contributes one value; a master profile
# accumulates every distinct value it has ever been seen with).
ARRAY_IDENTITY_FIELDS = {
    "device_id": "device_ids",
    "advertising_id": "advertising_ids",
    "cookie_id": "cookie_ids",
}

# Per-source-system customer identifiers, consolidated into a JSONB map on the
# master profile: {"AppsFlyer": "...", "MoEngage": "...", ...}.
JSONB_KEYED_IDENTITY_FIELDS = {
    "external_customer_id": "external_ids",
}

RAW_PROFILE_COLUMNS = (
    "raw_profile_id",
    "tenant_id",
    "domain",
    "source_system",
    "created_at",
    "event_name",
    "event_time",
    "event_payload",
    "full_name",
    "email",
    "phone_number",
    "national_id",
    "device_id",
    "advertising_id",
    "cookie_id",
    "external_customer_id",
    "push_token",
)


class CustomerIdentityResolver:
    """Links raw profiles to master profiles using dynamically configured
    matching rules stored in ``cdp_profile_attributes``, scoped per tenant
    and per domain (retail/banking/real_estate/travel/media/education).
    """

    def __init__(
        self,
        db_connection,
        schema: str = "customer360",
        batch_size: int = 1000,
        enable_persona_resolution: bool = True,
    ):
        """
        Args:
            db_connection: A psycopg2 connection object (or a connection pool
                checkout), injected for easy unit testing.
            schema: The database schema containing the CDP tables.
            batch_size: Number of records to process per batch/run.
            enable_persona_resolution: When True (default), every raw profile
                processed by run_resolution_batch also gets its master
                profile's persona recomputed via PersonaResolutionEngine
                (identity *understanding* on top of identity *matching*).
                PersonaResolutionEngine.resolve_persona() never raises, so
                this can never abort/rollback the surrounding CIR batch --
                disable only for tests that want to assert on the matching
                logic in isolation without persona-engine side effects.
        """
        self.conn = db_connection
        self.schema = schema
        self.batch_size = batch_size
        self.persona_engine = PersonaResolutionEngine(schema=schema) if enable_persona_resolution else None

    def _table(self, name: str) -> str:
        return f"{self.schema}.{name}" if self.schema else name

    def _get_active_rules(self, cursor) -> List[IdentityRule]:
        """Fetches active identity resolution rules from the metadata table."""
        query = f"""
            SELECT
                attribute_internal_code,
                matching_rule,
                matching_threshold,
                consolidation_rule,
                consolidation_config
            FROM {self._table('cdp_profile_attributes')}
            WHERE is_identity_resolution = TRUE
              AND status = 'ACTIVE'
              AND matching_rule IS NOT NULL
              AND matching_rule != 'none';
        """
        cursor.execute(query)
        return [
            IdentityRule(
                attribute_code=row["attribute_internal_code"],
                match_rule=row["matching_rule"],
                threshold=row["matching_threshold"],
                consolidation_rule=row.get("consolidation_rule"),
                consolidation_config=row.get("consolidation_config"),
            )
            for row in cursor.fetchall()
        ]

    def _fetch_master_profile_state(
        self,
        cursor,
        tenant_id: str,
        master_id: str,
        columns: Sequence[str] = BASE_MASTER_STATE_COLUMNS,
    ) -> Dict[str, Any]:
        """Fetches the current master row values needed for merge precedence."""
        column_list = ", ".join(columns)
        query = f"""
            SELECT {column_list}
            FROM {self._table('cdp_master_profiles')}
            WHERE tenant_id = %s AND master_profile_id = %s
            LIMIT 1;
        """
        cursor.execute(query, (tenant_id, master_id))
        row = cursor.fetchone() or {}
        return dict(row)

    @staticmethod
    def _collect_master_state_columns(rule_map: Dict[str, IdentityRule]) -> List[str]:
        """Base master columns plus any extra verified_field/timestamp_field
        named by active consolidation_config, so a custom field (e.g. a
        banking-specific verification column) actually gets fetched instead
        of silently reading back as None."""
        columns: List[str] = list(BASE_MASTER_STATE_COLUMNS)
        for rule in rule_map.values():
            config = rule.consolidation_config
            if not isinstance(config, dict):
                continue
            for key in ("verified_field", "timestamp_field"):
                value = config.get(key)
                if not isinstance(value, str) or value in columns:
                    continue
                # Domain attributes live in cdp_domain_profiles.domain_attributes,
                # not as scalar columns on cdp_master_profiles.
                if value in DOMAIN_ATTRIBUTE_FIELDS:
                    continue
                if _SAFE_IDENTIFIER_RE.match(value):
                    columns.append(value)
                else:
                    logger.warning(
                        "Ignoring unsafe consolidation_config column name '%s' for '%s'.", value, key
                    )
        return columns

    def _fetch_domain_attributes(
        self,
        cursor,
        tenant_id: str,
        master_id: str,
        domain_code: Optional[str],
    ) -> Dict[str, Any]:
        query = f"""
            SELECT dp.domain_attributes
            FROM {self._table('cdp_domain_profiles')} dp
            JOIN {self._table('sys_domain')} d
              ON d.domain_id = dp.domain_id
            WHERE dp.tenant_id = %s
              AND dp.master_profile_id = %s
              AND d.domain_code = %s
            LIMIT 1;
        """
        cursor.execute(query, (tenant_id, master_id, domain_code or "retail"))
        row = cursor.fetchone() or {}
        attrs = row.get("domain_attributes")
        return attrs if isinstance(attrs, dict) else {}

    def _upsert_domain_attributes(
        self,
        cursor,
        tenant_id: str,
        master_id: str,
        domain_code: Optional[str],
        attrs: Dict[str, Any],
    ) -> None:
        if not attrs:
            return
        query = f"""
            INSERT INTO {self._table('cdp_domain_profiles')} (
                tenant_id,
                master_profile_id,
                domain_id,
                domain_attributes,
                created_at,
                updated_at
            )
            VALUES (
                %s,
                %s,
                (SELECT domain_id FROM {self._table('sys_domain')} WHERE domain_code = %s LIMIT 1),
                %s,
                NOW(),
                NOW()
            )
            ON CONFLICT (master_profile_id, domain_id)
            DO UPDATE SET
                domain_attributes = COALESCE(cdp_domain_profiles.domain_attributes, '{{}}'::jsonb) || EXCLUDED.domain_attributes,
                updated_at = NOW();
        """
        cursor.execute(query, (tenant_id, master_id, domain_code or "retail", Json(attrs)))

    @staticmethod
    def _has_value(value: Any) -> bool:
        return value not in (None, "", [], {}, ())

    @staticmethod
    def _unique_sequence(values: Sequence[Any]) -> List[Any]:
        unique_values: List[Any] = []
        for value in values:
            if value not in unique_values:
                unique_values.append(value)
        return unique_values

    def _merge_append_distinct(self, current_value: Any, incoming_value: Any) -> Any:
        if not self._has_value(current_value):
            return incoming_value
        if not self._has_value(incoming_value):
            return current_value

        if isinstance(current_value, list) or isinstance(incoming_value, list):
            current_items = current_value if isinstance(current_value, list) else [current_value]
            incoming_items = incoming_value if isinstance(incoming_value, list) else [incoming_value]
            return self._unique_sequence(list(current_items) + list(incoming_items))

        if current_value == incoming_value:
            return current_value

        return self._unique_sequence([current_value, incoming_value])

    @staticmethod
    def _source_priority_rank(source_system: Optional[str], priority: Sequence[str]) -> int:
        """Lower rank wins. Matching is case-insensitive so config/casing
        drift (e.g. 'AppsFlyer' vs 'appsflyer') never silently breaks this
        policy -- values not found in ``priority`` rank last."""
        if not source_system:
            return len(priority)
        normalized_priority = [str(item).casefold() for item in priority]
        try:
            return normalized_priority.index(str(source_system).casefold())
        except ValueError:
            return len(priority)

    @staticmethod
    def _lookup_field(entity: Dict[str, Any], field: str) -> Any:
        """Reads ``field`` off ``entity``, falling back to a same-named key
        nested inside ``entity['event_payload']`` -- cdp_raw_profiles_stage
        carries most business signals (e.g. a KYC verification flag) inside
        the free-form event_payload JSONB rather than as a dedicated column."""
        value = entity.get(field)
        if value is not None:
            return value
        payload = entity.get("event_payload")
        if isinstance(payload, dict):
            return payload.get(field)
        return None

    # =========================================================================
    # CONSOLIDATION STRATEGY IMPLEMENTATIONS
    # =========================================================================
    # Each strategy method handles one consolidation_rule type, returning the
    # merged value to be used. These are called from _resolve_scalar_consolidation
    # to reduce method complexity and improve testability.

    def _consolidate_overwrite(self, current_value: Any, incoming_value: Any) -> Any:
        """Overwrite: always take incoming value."""
        return incoming_value

    def _consolidate_non_null(self, current_value: Any, incoming_value: Any) -> Any:
        """Non-null: prefer current if it has a value, else take incoming."""
        return current_value if self._has_value(current_value) else incoming_value

    def _consolidate_most_recent(
        self,
        current_value: Any,
        incoming_value: Any,
        raw_profile: Dict[str, Any],
        current_master: Dict[str, Any],
        config: Dict[str, Any],
    ) -> Any:
        """Most-recent: compare timestamps and prefer the more recent value."""
        timestamp_field = config.get("timestamp_field", "updated_at")
        incoming_timestamp = (
            raw_profile.get(timestamp_field)
            or raw_profile.get("event_time")
            or raw_profile.get("created_at")
        )
        current_timestamp = current_master.get(timestamp_field) or current_master.get("updated_at")
        comparison = self._is_incoming_more_recent(incoming_timestamp, current_timestamp)
        if comparison is not None:
            return incoming_value if comparison else current_value
        return incoming_value if not self._has_value(current_value) else current_value

    def _consolidate_verified_first(
        self,
        current_value: Any,
        incoming_value: Any,
        raw_profile: Dict[str, Any],
        current_master: Dict[str, Any],
        config: Dict[str, Any],
        rule: IdentityRule,
    ) -> Any:
        """Verified-first: prefer verified values; on tie, use fallback strategy."""
        verified_field = config.get("verified_field", "kyc_status")
        verified_values = config.get("verified_values", ["verified"])
        if not isinstance(verified_values, list):
            verified_values = [verified_values]

        current_is_verified = self._lookup_field(current_master, verified_field) in verified_values
        incoming_is_verified = self._raw_profile_is_verified(
            raw_profile, verified_field, verified_values, config
        )

        if current_is_verified and not incoming_is_verified:
            return current_value
        if incoming_is_verified and not current_is_verified:
            return incoming_value

        # Both verified or both unverified: use fallback strategy
        fallback_mode = config.get("fallback_mode")
        # For verified_then_most_recent with no explicit fallback, use most_recent
        if not fallback_mode and rule.consolidation_rule == "verified_then_most_recent":
            fallback_mode = "most_recent"
        # Otherwise, verify fallback_mode isn't recursive and use non_null default
        elif fallback_mode in {"verified_first", "verified_then_most_recent"}:
            logger.warning(
                "Invalid fallback_mode '%s' - using 'non_null' instead.",
                fallback_mode,
            )
            fallback_mode = "non_null"
        elif not fallback_mode:
            fallback_mode = "non_null"

        return self._resolve_scalar_consolidation(
            rule.attribute_code,
            current_value,
            incoming_value,
            IdentityRule(
                attribute_code=rule.attribute_code,
                match_rule=rule.match_rule,
                threshold=rule.threshold,
                consolidation_rule=fallback_mode,
                consolidation_config=config,
            ),
            raw_profile,
            current_master,
        )

    def _consolidate_source_priority(
        self,
        current_value: Any,
        incoming_value: Any,
        raw_profile: Dict[str, Any],
        current_master: Dict[str, Any],
        config: Dict[str, Any],
    ) -> Any:
        """Source-priority: prefer values from higher-priority source systems."""
        priority = config.get("source_priority", [])
        if not isinstance(priority, list):
            priority = [priority]
        incoming_rank = self._source_priority_rank(raw_profile.get("source_system"), priority)
        current_sources = current_master.get("source_systems") or []
        current_rank = (
            min(self._source_priority_rank(source, priority) for source in current_sources)
            if current_sources
            else len(priority)
        )

        if incoming_rank < current_rank:
            return incoming_value
        if current_rank < incoming_rank:
            return current_value

        return incoming_value if not self._has_value(current_value) else current_value

    def _consolidate_append_distinct(self, current_value: Any, incoming_value: Any) -> Any:
        """Append-distinct: merge into unique list."""
        return self._merge_append_distinct(current_value, incoming_value)

    @staticmethod
    def _is_incoming_more_recent(incoming_timestamp: Any, current_timestamp: Any) -> Optional[bool]:
        """True/False, or None if the two timestamps can't be meaningfully
        compared (missing, or a naive/timezone-aware mismatch)."""
        if incoming_timestamp is None or current_timestamp is None:
            return None
        try:
            return incoming_timestamp >= current_timestamp
        except TypeError:
            logger.warning(
                "Could not compare timestamps %r and %r for most_recent consolidation.",
                incoming_timestamp,
                current_timestamp,
            )
            return None

    @staticmethod
    def _extract_communication_preferences(raw_profile: Dict[str, Any]) -> Dict[str, Any]:
        """Returns communication_preferences payload when present and valid.

        Raw profiles do not have a dedicated communication_preferences column;
        integrations can still send it inside event_payload JSON.
        """
        payload = raw_profile.get("event_payload")
        if not isinstance(payload, dict):
            return {}
        preferences = payload.get("communication_preferences")
        return preferences if isinstance(preferences, dict) else {}

    def _raw_profile_is_verified(
        self,
        raw_profile: Dict[str, Any],
        verified_field: str,
        verified_values: List[Any],
        config: Dict[str, Any],
    ) -> bool:
        # cdp_raw_profiles_stage has no dedicated verification column (e.g.
        # kyc_status only exists on cdp_master_profiles) -- verified_event_names
        # lets config treat a specific business event (e.g. 'kyc-completed')
        # on the incoming raw profile itself as proof of verification.
        event_names = config.get("verified_event_names")
        if event_names:
            if not isinstance(event_names, list):
                event_names = [event_names]
            if raw_profile.get("event_name") in event_names:
                return True
        return self._lookup_field(raw_profile, verified_field) in verified_values

    def _resolve_scalar_consolidation(
        self,
        field_name: str,
        current_value: Any,
        incoming_value: Any,
        rule: Optional[IdentityRule],
        raw_profile: Dict[str, Any],
        current_master: Dict[str, Any],
    ) -> Any:
        """Resolve which value to keep based on consolidation strategy.
        Delegates to strategy-specific methods for cleaner separation."""
        if not self._has_value(incoming_value):
            return current_value

        if rule is None or not rule.consolidation_rule:
            return incoming_value if not self._has_value(current_value) else current_value

        config = rule.consolidation_config or {}
        if not isinstance(config, dict):
            config = {}

        strategy = rule.consolidation_rule

        # Dispatch to strategy-specific handler methods for cleaner code structure
        strategy_handlers = {
            "overwrite": lambda: self._consolidate_overwrite(current_value, incoming_value),
            "non_null": lambda: self._consolidate_non_null(current_value, incoming_value),
            "most_recent": lambda: self._consolidate_most_recent(
                current_value, incoming_value, raw_profile, current_master, config
            ),
            "verified_first": lambda: self._consolidate_verified_first(
                current_value, incoming_value, raw_profile, current_master, config, rule
            ),
            "verified_then_most_recent": lambda: self._consolidate_verified_first(
                current_value, incoming_value, raw_profile, current_master, config, rule
            ),
            "source_priority": lambda: self._consolidate_source_priority(
                current_value, incoming_value, raw_profile, current_master, config
            ),
            "append_distinct": lambda: self._consolidate_append_distinct(current_value, incoming_value),
        }

        if strategy in strategy_handlers:
            return strategy_handlers[strategy]()

        logger.warning(
            "Unknown consolidation_rule '%s' for attribute '%s' - falling back to non_null.",
            strategy,
            field_name,
        )
        return current_value if self._has_value(current_value) else incoming_value

    # =========================================================================
    # MATCH CONDITION BUILDING
    # =========================================================================

    def _build_match_condition(
        self,
        rule: IdentityRule,
        raw_profile: Dict[str, Any],
        code: str,
        raw_value: Any,
    ) -> Optional[tuple]:
        """Build a single match condition for a given rule.
        
        Returns:
            Tuple of (condition_sql, params, field_code) or None if condition can't be built.
            Centralizes condition building logic for exact, fuzzy_trgm, fuzzy_dmetaphone,
            array, and JSONB field matching to reduce duplication in _find_master_profile.
        """
        source_system = raw_profile.get("source_system")

        # Array/JSONB identity field matching
        if code in ARRAY_IDENTITY_FIELDS:
            return (
                f"%s = ANY({ARRAY_IDENTITY_FIELDS[code]})",
                [raw_value],
                code,
            )
        if code in JSONB_KEYED_IDENTITY_FIELDS:
            if not source_system:
                return None
            column = JSONB_KEYED_IDENTITY_FIELDS[code]
            return (
                f"{column} @> jsonb_build_object(%s::text, %s::text)",
                [source_system, raw_value],
                code,
            )

        # Exact match (scalar or domain attribute)
        if rule.match_rule == "exact":
            if code in DOMAIN_ATTRIBUTE_FIELDS:
                domain_code = raw_profile.get("domain", "retail")
                return (
                    f"EXISTS (SELECT 1 FROM {self._table('cdp_domain_profiles')} dp "
                    f"JOIN {self._table('sys_domain')} sd ON sd.domain_id = dp.domain_id "
                    "WHERE dp.master_profile_id = cdp_master_profiles.master_profile_id "
                    "AND dp.tenant_id = cdp_master_profiles.tenant_id "
                    "AND sd.domain_code = %s AND dp.domain_attributes ->> %s = %s)",
                    [domain_code, code, raw_value],
                    code,
                )
            return (f"{code} = %s", [raw_value], code)

        # Fuzzy matching (trgm similarity)
        if rule.match_rule == "fuzzy_trgm":
            threshold = rule.threshold if rule.threshold is not None else 0.7
            if code in DOMAIN_ATTRIBUTE_FIELDS:
                domain_code = raw_profile.get("domain", "retail")
                return (
                    f"EXISTS (SELECT 1 FROM {self._table('cdp_domain_profiles')} dp "
                    f"JOIN {self._table('sys_domain')} sd ON sd.domain_id = dp.domain_id "
                    "WHERE dp.master_profile_id = cdp_master_profiles.master_profile_id "
                    "AND dp.tenant_id = cdp_master_profiles.tenant_id "
                    "AND sd.domain_code = %s AND similarity(dp.domain_attributes ->> %s, %s) >= %s)",
                    [domain_code, code, raw_value, threshold],
                    code,
                )
            return (
                f"similarity({code}, %s) >= %s",
                [raw_value, threshold],
                code,
            )

        # Fuzzy matching (dmetaphone)
        if rule.match_rule == "fuzzy_dmetaphone":
            if code in DOMAIN_ATTRIBUTE_FIELDS:
                domain_code = raw_profile.get("domain", "retail")
                return (
                    f"EXISTS (SELECT 1 FROM {self._table('cdp_domain_profiles')} dp "
                    f"JOIN {self._table('sys_domain')} sd ON sd.domain_id = dp.domain_id "
                    "WHERE dp.master_profile_id = cdp_master_profiles.master_profile_id "
                    "AND dp.tenant_id = cdp_master_profiles.tenant_id "
                    "AND sd.domain_code = %s AND dmetaphone(dp.domain_attributes ->> %s) = dmetaphone(%s))",
                    [domain_code, code, raw_value],
                    code,
                )
            return (
                f"dmetaphone({code}) = dmetaphone(%s)",
                [raw_value],
                code,
            )

        logger.warning(
            "Unknown matching_rule '%s' for attribute '%s' - skipping.",
            rule.match_rule,
            code,
        )
        return None

    def _fetch_tenant_ids(self, cursor) -> List[str]:
        """Fetch tenant IDs before entering each tenant's RLS context."""
        set_tenant_context(cursor, None)
        cursor.execute(f"SELECT tenant_id FROM {self._table('sys_tenant')} ORDER BY tenant_id;")
        return [str(row["tenant_id"] if isinstance(row, dict) else row[0]) for row in cursor.fetchall()]

    def _fetch_unprocessed_profiles(self, cursor, tenant_id: str) -> List[Dict[str, Any]]:
        """Fetch a batch of this tenant's raw profiles not yet processed."""
        columns = ", ".join(RAW_PROFILE_COLUMNS)
        query = f"""
            SELECT {columns}
            FROM {self._table('cdp_raw_profiles_stage')}
            WHERE tenant_id = %s AND status_code = 1
            LIMIT %s;
        """
        cursor.execute(query, (tenant_id, self.batch_size))
        return cursor.fetchall()

    def _find_master_profile(
        self,
        cursor,
        raw_profile: Dict[str, Any],
        rules: List[IdentityRule],
        return_details: bool = False,
    ) -> Optional[Any]:
        """Dynamically builds and executes a query to find a matching master
        profile, scoped to the same tenant and domain, based on the active
        metadata rules. Delegates condition building to _build_match_condition
        to reduce duplication and improve readability."""
        conditions = []
        params: List[Any] = []
        condition_fields: List[str] = []

        for rule in rules:
            code = rule.attribute_code
            raw_value = raw_profile.get(code)
            if not raw_value:
                continue

            # Build condition using centralized builder method
            condition_result = self._build_match_condition(rule, raw_profile, code, raw_value)
            if condition_result:
                condition_sql, condition_params, field_code = condition_result
                conditions.append(condition_sql)
                params.extend(condition_params)
                condition_fields.append(field_code)

        if not conditions:
            return None

        where_clause = " OR ".join(f"({c})" for c in conditions)

        if not return_details:
            query = f"""
                SELECT master_profile_id
                FROM {self._table('cdp_master_profiles')}
                WHERE tenant_id = %s AND domain = %s AND ({where_clause})
                LIMIT 1;
            """
            query_params = [raw_profile["tenant_id"], raw_profile.get("domain", "retail")] + params
            cursor.execute(query, tuple(query_params))
            result = cursor.fetchone()
            return result["master_profile_id"] if result else None

        # Each condition is also projected as m_<idx> so we can compute an
        # actual per-link score and preserve the exact fields that matched.
        match_case_columns = [
            f"CASE WHEN ({condition}) THEN 1 ELSE 0 END AS m_{idx}"
            for idx, condition in enumerate(conditions)
        ]
        score_expr = " + ".join(f"m_{idx}" for idx in range(len(conditions)))
        score_denominator = float(len(conditions))

        query = f"""
            SELECT
                master_profile_id,
                ({score_expr})::DOUBLE PRECISION / %s AS match_score,
                {", ".join(f"m_{idx}" for idx in range(len(conditions)))}
            FROM (
                SELECT
                    master_profile_id,
                    {", ".join(match_case_columns)}
                FROM {self._table('cdp_master_profiles')}
                WHERE tenant_id = %s AND domain = %s AND ({where_clause})
            ) candidates
            ORDER BY ({score_expr}) DESC
            LIMIT 1;
        """
        query_params = [score_denominator] + params + [raw_profile["tenant_id"], raw_profile.get("domain", "retail")] + params
        cursor.execute(query, tuple(query_params))
        result = cursor.fetchone()
        if not result:
            return None

        matched_fields: List[str] = []
        for idx, field in enumerate(condition_fields):
            if result.get(f"m_{idx}") == 1 and field not in matched_fields:
                matched_fields.append(field)

        score = result.get("match_score")
        method_suffix = ",".join(matched_fields)
        match_method = f"DynamicMatch:{method_suffix}" if method_suffix else "DynamicMatch"

        return {
            "master_profile_id": result["master_profile_id"],
            "match_score": float(score) if score is not None else None,
            "matched_fields": matched_fields,
            "match_method": match_method,
        }

    def _link_and_update(
        self,
        cursor,
        raw_profile: Dict[str, Any],
        master_id: str,
        match_method: str = "DynamicMatch",
        match_score: Optional[float] = 1.0,
        rules: Optional[List[IdentityRule]] = None,
    ) -> None:
        """Links the raw profile to an existing master profile and merges any
        new identity data (COALESCE for scalars, append-distinct for identity
        arrays/maps)."""
        link_query = f"""
            INSERT INTO {self._table('cdp_profile_links')}
                (tenant_id, raw_profile_id, master_profile_id, match_score, match_method)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, raw_profile_id) DO NOTHING;
        """
        cursor.execute(
            link_query,
            (
                raw_profile["tenant_id"],
                raw_profile["raw_profile_id"],
                master_id,
                match_score,
                match_method,
            ),
        )

        source_system = raw_profile.get("source_system")
        rule_map = {rule.attribute_code: rule for rule in rules or []}
        has_consolidation_metadata = any(
            rule.consolidation_rule or rule.consolidation_config for rule in rule_map.values()
        )

        current_master: Dict[str, Any] = {}
        current_domain_attrs: Dict[str, Any] = {}
        if has_consolidation_metadata:
            columns = self._collect_master_state_columns(rule_map)
            current_master = self._fetch_master_profile_state(
                cursor,
                str(raw_profile["tenant_id"]),
                master_id,
                columns,
            )
            current_domain_attrs = self._fetch_domain_attributes(
                cursor,
                str(raw_profile["tenant_id"]),
                str(master_id),
                raw_profile.get("domain", "retail"),
            )
            current_master.update(current_domain_attrs)
            set_clauses = []
            params = []
            for field in SCALAR_MERGE_FIELDS:
                merged_value = self._resolve_scalar_consolidation(
                    field,
                    current_master.get(field),
                    raw_profile.get(field),
                    rule_map.get(field),
                    raw_profile,
                    current_master,
                )
                if isinstance(merged_value, list):
                    # SCALAR_MERGE_FIELDS are all TEXT columns -- append_distinct
                    # (meant for array-typed attributes) must never leak a
                    # Python list into a scalar UPDATE parameter.
                    merged_value = ", ".join(str(v) for v in merged_value if self._has_value(v)) or None
                set_clauses.append(f"{field} = %s")
                params.append(merged_value)
        else:
            set_clauses = [f"{field} = COALESCE({field}, %s)" for field in SCALAR_MERGE_FIELDS]
            params = [raw_profile.get(field) for field in SCALAR_MERGE_FIELDS]

        # Always preserve the explicitly-provided domain on the master row so
        # that profiles stay correctly classified across retail/banking/
        # real_estate/travel/media/education.
        set_clauses.append("domain = %s")
        params.append(raw_profile.get("domain", "retail"))

        for raw_field, master_col in ARRAY_IDENTITY_FIELDS.items():
            value = raw_profile.get(raw_field)
            if not value:
                continue
            set_clauses.append(
                f"{master_col} = CASE WHEN %s = ANY(COALESCE({master_col}, ARRAY[]::TEXT[])) "
                f"THEN {master_col} ELSE array_append(COALESCE({master_col}, ARRAY[]::TEXT[]), %s) END"
            )
            params.extend([value, value])

        if source_system:
            for raw_field, master_col in JSONB_KEYED_IDENTITY_FIELDS.items():
                value = raw_profile.get(raw_field)
                if not value:
                    continue
                set_clauses.append(
                    f"{master_col} = COALESCE({master_col}, '{{}}'::JSONB) "
                    f"|| jsonb_build_object(%s::text, %s::text)"
                )
                params.extend([source_system, value])

            push_token = raw_profile.get("push_token")
            if push_token:
                set_clauses.append(
                    "push_tokens = COALESCE(push_tokens, '{}'::JSONB) "
                    "|| jsonb_build_object(%s::text, %s::text)"
                )
                params.extend([source_system, push_token])

            set_clauses.append(
                "source_systems = CASE WHEN %s = ANY(COALESCE(source_systems, ARRAY[]::TEXT[])) "
                "THEN source_systems ELSE array_append(COALESCE(source_systems, ARRAY[]::TEXT[]), %s) END"
            )
            params.extend([source_system, source_system])

        communication_preferences = self._extract_communication_preferences(raw_profile)
        if communication_preferences:
            set_clauses.append(
                "communication_preferences = COALESCE(communication_preferences, '{}'::JSONB) || %s::JSONB"
            )
            params.append(Json(communication_preferences))

        # is_hashed/persona_name: whenever this (or an earlier) contributing
        # raw profile's PII looks SHA-256 hashed, the master profile is
        # flagged as hashed and MUST carry a persona_name (readable label
        # standing in for the now-unreadable PII) -- see persona.py and the
        # cdp_master_profiles CHECK constraint in database-schema.sql.
        looks_hashed = profile_looks_hashed(raw_profile)
        set_clauses.append("is_hashed = is_hashed OR %s")
        params.append(looks_hashed)
        set_clauses.append("persona_name = COALESCE(persona_name, %s)")
        params.append(generate_persona_name(raw_profile) if looks_hashed else None)

        set_clauses.append("updated_at = NOW()")
        params.extend([master_id, raw_profile["tenant_id"]])

        update_query = f"""
            UPDATE {self._table('cdp_master_profiles')}
            SET {", ".join(set_clauses)}
            WHERE master_profile_id = %s AND tenant_id = %s;
        """
        cursor.execute(update_query, tuple(params))

        incoming_domain_values: Dict[str, Any] = {}
        for field in DOMAIN_ATTRIBUTE_FIELDS:
            field_value = self._lookup_field(raw_profile, field)
            if self._has_value(field_value):
                incoming_domain_values[field] = field_value
        if not incoming_domain_values:
            return

        domain_attr_updates: Dict[str, Any] = {}
        if not has_consolidation_metadata:
            current_domain_attrs = self._fetch_domain_attributes(
                cursor,
                str(raw_profile["tenant_id"]),
                str(master_id),
                raw_profile.get("domain", "retail"),
            )

        current_master_with_domain = dict(current_master) if has_consolidation_metadata else {}
        current_master_with_domain.update(current_domain_attrs)
        for field, incoming_value in incoming_domain_values.items():
            merged_value = self._resolve_scalar_consolidation(
                field,
                current_domain_attrs.get(field),
                incoming_value,
                rule_map.get(field),
                raw_profile,
                current_master_with_domain,
            )
            if self._has_value(merged_value):
                domain_attr_updates[field] = merged_value
        if domain_attr_updates:
            self._upsert_domain_attributes(
                cursor,
                str(raw_profile["tenant_id"]),
                str(master_id),
                raw_profile.get("domain", "retail"),
                domain_attr_updates,
            )

    def _create_master_and_link(self, cursor, raw_profile: Dict[str, Any]) -> str:
        """Creates a brand new master profile when no match was found and
        links the raw profile to it. Returns the new master_profile_id."""
        source_system = raw_profile.get("source_system")

        external_ids = {}
        if source_system and raw_profile.get("external_customer_id"):
            external_ids[source_system] = raw_profile["external_customer_id"]

        push_tokens = {}
        if source_system and raw_profile.get("push_token"):
            push_tokens[source_system] = raw_profile["push_token"]

        device_ids = [raw_profile["device_id"]] if raw_profile.get("device_id") else []
        advertising_ids = [raw_profile["advertising_id"]] if raw_profile.get("advertising_id") else []
        cookie_ids = [raw_profile["cookie_id"]] if raw_profile.get("cookie_id") else []
        source_systems = [source_system] if source_system else []

        # is_hashed/persona_name: see the matching comment in _link_and_update.
        looks_hashed = profile_looks_hashed(raw_profile)
        persona_name = generate_persona_name(raw_profile) if looks_hashed else None

        insert_master_query = f"""
            INSERT INTO {self._table('cdp_master_profiles')}
                (tenant_id, domain, full_name, email, phone_number,
                 external_ids, device_ids, advertising_ids, cookie_ids, push_tokens,
                 source_systems, first_seen_raw_profile_id, is_hashed, persona_name)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING master_profile_id;
        """
        cursor.execute(
            insert_master_query,
            (
                raw_profile["tenant_id"],
                # Domain defaults to "retail" for backwards compatibility if
                # the source omits it; valid domains now include media/education.
                raw_profile.get("domain", "retail"),
                raw_profile.get("full_name"),
                raw_profile.get("email"),
                raw_profile.get("phone_number"),
                Json(external_ids),
                device_ids,
                advertising_ids,
                cookie_ids,
                Json(push_tokens),
                source_systems,
                raw_profile["raw_profile_id"],
                looks_hashed,
                persona_name,
            ),
        )
        new_master_id = cursor.fetchone()["master_profile_id"]

        national_id = raw_profile.get("national_id")
        if national_id:
            self._upsert_domain_attributes(
                cursor,
                str(raw_profile["tenant_id"]),
                str(new_master_id),
                raw_profile.get("domain", "retail"),
                {"national_id": national_id},
            )

        link_query = f"""
            INSERT INTO {self._table('cdp_profile_links')}
                (tenant_id, raw_profile_id, master_profile_id, match_score, match_method)
            VALUES (%s, %s, %s, %s, %s);
        """
        cursor.execute(
            link_query,
            (raw_profile["tenant_id"], raw_profile["raw_profile_id"], new_master_id, None, "NewMaster"),
        )
        return new_master_id

    def _mark_as_processed(self, cursor, tenant_id: str, raw_profile_id: str) -> None:
        """Marks a raw profile as processed (status_code = 3) and stamps processed_at."""
        query = f"""
            UPDATE {self._table('cdp_raw_profiles_stage')}
            SET status_code = 3, processed_at = NOW()
            WHERE tenant_id = %s AND raw_profile_id = %s;
        """
        cursor.execute(query, (tenant_id, raw_profile_id))

    def run_resolution_batch(self) -> int:
        """Main orchestration method for a single batch run. Idempotent and
        safe to retry: only rows with ``status_code = 1`` are touched, and
        links are protected by the unique constraint on
        ``(tenant_id, raw_profile_id)``.

        Returns:
            The number of raw profiles processed in this batch.
        """
        try:
            with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
                set_tenant_context(cursor, None)
                rules = self._get_active_rules(cursor)
                if not rules:
                    logger.warning("No active identity resolution rules found. Aborting.")
                    return 0

                total_processed = 0
                for tenant_id in self._fetch_tenant_ids(cursor):
                    set_tenant_context(cursor, tenant_id)
                    raw_profiles = self._fetch_unprocessed_profiles(cursor, tenant_id)
                    if not raw_profiles:
                        continue

                    logger.info("Processing batch of %d profiles for tenant %s.", len(raw_profiles), tenant_id)

                    for profile in raw_profiles:
                        matched = self._find_master_profile(cursor, profile, rules, return_details=True)

                        if matched:
                            self._link_and_update(
                                cursor,
                                profile,
                                matched["master_profile_id"],
                                match_method=matched.get("match_method", "DynamicMatch"),
                                match_score=matched.get("match_score"),
                                rules=rules,
                            )
                            matched_id = matched["master_profile_id"]
                        else:
                            matched_id = self._create_master_and_link(cursor, profile)

                        # Identity *understanding*: recompute the resolved master
                        # profile's persona without changing the active RLS tenant.
                        if self.persona_engine is not None:
                            self.persona_engine.resolve_persona(cursor, tenant_id, matched_id)

                        self._mark_as_processed(cursor, tenant_id, profile["raw_profile_id"])
                        total_processed += 1

                if total_processed == 0:
                    logger.info("No unprocessed profiles found in staging.")
                    return 0

                self.conn.commit()
                logger.info("Batch processed and committed successfully.")
                return total_processed

        except Exception:
            self.conn.rollback()
            logger.exception("Error during identity resolution.")
            raise
