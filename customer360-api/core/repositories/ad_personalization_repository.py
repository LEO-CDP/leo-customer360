"""Persistence for the ad personalisation admin plane (``leo_ads`` schema).

Reads and writes ``leo_ads.personalization_setting`` for the tenant of the
caller. Design: specs/003-ad-personalization-engine (data-model.md,
contracts/admin-plane.openapi.yaml).

Tenant isolation
    The caller's platform tenant (a UUID from the token, never from the request
    body) is mapped to the ad tenant through ``leo_ads.tenant.customer360_tenant_id``.
    Every later query filters by that ad tenant id. ``leo_ads`` has no
    row-level security, so this mapping and filtering is the whole isolation
    story; each method takes the resolved id as a required argument.

The ``upsert_setting`` rules deliberately match the data plane's
``SettingRepository`` in customer360-promotions: hold-out between 0 and 100,
consent mode and a new measurement period only on the tenant scope, and a
tenant row before any site or placement row.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

SCOPE_TYPES = ("tenant", "site", "placement")
CONSENT_MODES = ("opt_in", "opt_out")

_SETTING_COLUMNS = """
    scope_type, scope_ref, enabled, holdout_pct, holdout_salt,
    consent_mode, updated_by, updated_at
"""


_ERASURE_COLUMNS = """
    erasure_id, status, urgent, requested_at, completed_at, deleted_counts
"""

DECISION_RETENTION_DAYS = 90

# Groups with fewer impressions than this report no rate (too noisy to compare).
# Override with AD_PERSONALIZATION_REPORT_MIN_IMPRESSIONS.
DEFAULT_MIN_SAMPLE = int(os.environ.get("AD_PERSONALIZATION_REPORT_MIN_IMPRESSIONS", "1000"))

# The only signal names the admin contract allows.
SIGNAL_NAMES = (
    "objective_rate_lift",
    "visitor_affinity",
    "cohort_pattern",
    "exploration",
    "base_weight",
    "page_context",
)

# Campaign objective -> (statistics column, contract metric name). The objective
# is free text in the catalogue, so each known type accepts its obvious aliases;
# anything else (including no objective) is judged on clicks.
_OBJECTIVE_METRICS = {
    "awareness": ("views", "view_rate"),
    "reach": ("views", "view_rate"),
    "brand_awareness": ("views", "view_rate"),
    "traffic": ("clicks", "click_through_rate"),
    "conversions": ("conversions", "conversion_rate"),
    "conversion": ("conversions", "conversion_rate"),
}
_DEFAULT_METRIC = ("clicks", "click_through_rate")
_REPORT_GROUPS = ("personalised", "holdout", "default")


def fingerprint(user_key: Optional[bytes]) -> Optional[str]:
    """First 4 bytes of a visitor key in hex: enough to correlate, useless to look up."""
    if user_key is None:
        return None
    return bytes(user_key)[:4].hex()


def explain_chosen(
    chosen: list[dict[str, Any]], ads: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Stored ``chosen`` entries as the contract's explanation, with ad keys and objectives."""
    out = []
    for entry in chosen:
        ad = ads.get(int(entry["ad_id"])) if entry.get("ad_id") is not None else None
        out.append(
            {
                "rank": int(entry.get("rank", len(out) + 1)),
                "ad_key": ad["ad_key"] if ad else f"ad:{entry.get('ad_id')}",
                "objective": ad["objective"] if ad and ad.get("objective") else None,
                "score": entry.get("score"),
                "signals": [
                    {
                        "name": s["name"],
                        "contribution": s.get("contribution"),
                        "note": s.get("note", ""),
                    }
                    for s in entry.get("signals", [])
                    if s.get("name") in SIGNAL_NAMES
                ],
            }
        )
    return out


def build_comparison(
    rows: list[dict[str, Any]],
    date_from: datetime,
    date_to: datetime,
    placement_key: Optional[str],
    min_sample: int = DEFAULT_MIN_SAMPLE,
) -> dict[str, Any]:
    """Shape statistics rows ``{group_code, objective, impressions, views, clicks, conversions}``.

    Pure, so the rate, lift and small-sample rules are testable without a database.
    A rate is reported only when BOTH personalised and hold-out have at least
    ``min_sample`` impressions for that objective; otherwise ``insufficient_data``.
    """
    counters = ("impressions", "views", "clicks", "conversions")
    totals = {g: dict.fromkeys(counters, 0) for g in _REPORT_GROUPS}
    per_objective: dict[str, dict[str, dict[str, int]]] = {}

    for row in rows:
        group = row["group_code"]
        if group not in totals:
            continue
        for name in counters:
            totals[group][name] += int(row[name] or 0)
        bucket = per_objective.setdefault(row["objective"], {}).setdefault(
            group, dict.fromkeys(counters, 0)
        )
        for name in counters:
            bucket[name] += int(row[name] or 0)

    by_objective = []
    for objective in sorted(per_objective):
        column, metric = _OBJECTIVE_METRICS.get(objective, _DEFAULT_METRIC)
        personalised = per_objective[objective].get("personalised", dict.fromkeys(counters, 0))
        holdout = per_objective[objective].get("holdout", dict.fromkeys(counters, 0))
        enough = (
            personalised["impressions"] >= min_sample and holdout["impressions"] >= min_sample
        )
        entry: dict[str, Any] = {
            "objective": objective,
            "metric": metric,
            "personalised_rate": None,
            "holdout_rate": None,
            "lift": None,
            "insufficient_data": not enough,
        }
        if enough:
            p_rate = personalised[column] / personalised["impressions"]
            h_rate = holdout[column] / holdout["impressions"]
            entry["personalised_rate"] = round(p_rate, 6)
            entry["holdout_rate"] = round(h_rate, 6)
            entry["lift"] = round((p_rate - h_rate) / h_rate, 4) if h_rate > 0 else None
        by_objective.append(entry)

    return {
        "from": date_from,
        "to": date_to,
        "placement_key": placement_key,
        "groups": [{"group": g, **totals[g]} for g in _REPORT_GROUPS],
        "by_objective": by_objective,
    }


class AdTenantNotFoundError(Exception):
    """The caller's tenant has no linked, active ad tenant."""


class AdPersonalizationValidationError(Exception):
    """A change breaks one of the personalisation rules (HTTP 422)."""


class AdPersonalizationRepository:
    """Tenant-scoped access to personalisation settings."""

    def __init__(self, session: Session):
        self.session = session

    # ------------------------------------------------------------------
    # Tenant mapping
    # ------------------------------------------------------------------

    def resolve_ad_tenant_id(self, platform_tenant_id: uuid.UUID | str) -> int:
        """Map the caller's platform tenant to the ad tenant id.

        Raises AdTenantNotFoundError when no active ad tenant is linked.
        """
        ad_tenant_id = self.session.execute(
            text(
                """
                SELECT tenant_id
                FROM leo_ads.tenant
                WHERE customer360_tenant_id = CAST(:platform_tenant AS uuid)
                  AND status = 'active'
                """
            ),
            {"platform_tenant": str(platform_tenant_id)},
        ).scalar_one_or_none()
        if ad_tenant_id is None:
            raise AdTenantNotFoundError("This tenant has no linked ad tenant")
        return int(ad_tenant_id)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def list_settings(self, ad_tenant_id: int) -> list[dict[str, Any]]:
        """All settings rows for the ad tenant, in a stable order."""
        rows = self.session.execute(
            text(
                f"""
                SELECT {_SETTING_COLUMNS}
                FROM leo_ads.personalization_setting
                WHERE tenant_id = :ad_tenant_id
                ORDER BY scope_type, scope_ref
                """
            ),
            {"ad_tenant_id": ad_tenant_id},
        ).mappings()
        return [self._setting(row) for row in rows]

    def upsert_setting(
        self,
        ad_tenant_id: int,
        *,
        scope_type: str,
        scope_ref: str = "",
        enabled: Optional[bool] = None,
        holdout_pct: Optional[float] = None,
        consent_mode: Optional[str] = None,
        new_measurement_period: bool = False,
        updated_by: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create or update one scope's row; only the fields provided change."""
        self._validate(
            scope_type, scope_ref, holdout_pct, consent_mode, new_measurement_period
        )
        if scope_type != "tenant" and not self._has_tenant_row(ad_tenant_id):
            raise AdPersonalizationValidationError(
                "Create the tenant-scope settings before a site or placement"
            )

        new_salt = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        row = self.session.execute(
            text(
                f"""
                INSERT INTO leo_ads.personalization_setting
                    (tenant_id, scope_type, scope_ref, enabled, holdout_pct,
                     holdout_salt, consent_mode, updated_by)
                VALUES
                    (:ad_tenant_id, :scope_type, :scope_ref,
                     COALESCE(:enabled, FALSE),
                     COALESCE(:holdout_pct, 10.00),
                     CASE WHEN :new_period THEN :new_salt ELSE 'default' END,
                     COALESCE(:consent_mode, 'opt_in'),
                     :updated_by)
                ON CONFLICT (tenant_id, scope_type, scope_ref) DO UPDATE SET
                    enabled = COALESCE(:enabled, leo_ads.personalization_setting.enabled),
                    holdout_pct = COALESCE(:holdout_pct, leo_ads.personalization_setting.holdout_pct),
                    consent_mode = COALESCE(:consent_mode, leo_ads.personalization_setting.consent_mode),
                    holdout_salt = CASE WHEN :new_period THEN :new_salt
                                        ELSE leo_ads.personalization_setting.holdout_salt END,
                    updated_by = COALESCE(:updated_by, leo_ads.personalization_setting.updated_by),
                    updated_at = now()
                RETURNING {_SETTING_COLUMNS}
                """
            ),
            {
                "ad_tenant_id": ad_tenant_id,
                "scope_type": scope_type,
                "scope_ref": scope_ref,
                "enabled": enabled,
                "holdout_pct": holdout_pct,
                "consent_mode": consent_mode,
                "new_period": new_measurement_period,
                "new_salt": new_salt,
                "updated_by": updated_by,
            },
        ).mappings().one()
        self.session.commit()
        return self._setting(row)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _validate(
        scope_type: str,
        scope_ref: str,
        holdout_pct: Optional[float],
        consent_mode: Optional[str],
        new_measurement_period: bool,
    ) -> None:
        if scope_type not in SCOPE_TYPES:
            raise AdPersonalizationValidationError(f"scope_type must be one of {SCOPE_TYPES}")
        if scope_type == "tenant" and scope_ref != "":
            raise AdPersonalizationValidationError("The tenant scope has an empty scope_ref")
        if scope_type != "tenant" and not scope_ref:
            raise AdPersonalizationValidationError(f"The {scope_type} scope needs a scope_ref")
        if holdout_pct is not None and not (0 <= float(holdout_pct) <= 100):
            raise AdPersonalizationValidationError("holdout_pct must be between 0 and 100")
        if consent_mode is not None:
            if consent_mode not in CONSENT_MODES:
                raise AdPersonalizationValidationError(
                    f"consent_mode must be one of {CONSENT_MODES}"
                )
            if scope_type != "tenant":
                raise AdPersonalizationValidationError(
                    "consent_mode can only be set on the tenant scope"
                )
        if new_measurement_period and scope_type != "tenant":
            raise AdPersonalizationValidationError(
                "A new measurement period can only be started on the tenant scope"
            )

    def _has_tenant_row(self, ad_tenant_id: int) -> bool:
        return (
            self.session.execute(
                text(
                    """
                    SELECT 1
                    FROM leo_ads.personalization_setting
                    WHERE tenant_id = :ad_tenant_id
                      AND scope_type = 'tenant' AND scope_ref = ''
                    """
                ),
                {"ad_tenant_id": ad_tenant_id},
            ).first()
            is not None
        )

    # ------------------------------------------------------------------
    # Explanations (serving decisions)
    # ------------------------------------------------------------------

    def get_decision(self, ad_tenant_id: int, request_id: str) -> Optional[dict[str, Any]]:
        """One stored serving decision of this tenant, or None.

        None also covers a decision older than the retention period and a
        decision that belongs to another tenant (it is simply not visible). The
        visitor key is returned only as a short fingerprint.
        """
        row = self.session.execute(
            text(
                """
                SELECT d.request_id, d.decided_at, d.group_code, d.candidate_count,
                       d.chosen, d.user_key_hash, p.placement_key
                FROM leo_ads.serving_decision d
                LEFT JOIN leo_ads.placement p
                       ON p.placement_id = d.placement_id AND p.tenant_id = d.tenant_id
                WHERE d.tenant_id = :ad_tenant_id
                  AND d.request_id = CAST(:request_id AS uuid)
                  AND d.decided_at >= now() - make_interval(days => :retention_days)
                ORDER BY d.decided_at DESC
                LIMIT 1
                """
            ),
            {
                "ad_tenant_id": ad_tenant_id,
                "request_id": str(request_id),
                "retention_days": DECISION_RETENTION_DAYS,
            },
        ).mappings().first()
        if row is None:
            return None

        chosen = row["chosen"] or []
        ads = self._ads_by_id(ad_tenant_id, [c.get("ad_id") for c in chosen])
        return {
            "request_id": str(row["request_id"]),
            "decided_at": row["decided_at"],
            "placement_key": row["placement_key"],
            "group": row["group_code"],
            "visitor_fingerprint": fingerprint(row["user_key_hash"]),
            "candidate_count": int(row["candidate_count"] or 0),
            "chosen": explain_chosen(chosen, ads),
        }

    def list_decisions(
        self,
        ad_tenant_id: int,
        ad_key: str,
        *,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        group: Optional[str] = None,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Recent decisions of this tenant that served ``ad_key``, newest first."""
        ad_id = self.session.execute(
            text("SELECT ad_id FROM leo_ads.ad WHERE tenant_id = :t AND ad_key = :k"),
            {"t": ad_tenant_id, "k": ad_key},
        ).scalar_one_or_none()
        if ad_id is None:
            return []

        rows = self.session.execute(
            text(
                """
                SELECT d.request_id, d.decided_at, d.group_code,
                       (c.item ->> 'rank')::int AS rank
                FROM leo_ads.serving_decision d
                CROSS JOIN LATERAL jsonb_array_elements(d.chosen) AS c(item)
                WHERE d.tenant_id = :ad_tenant_id
                  AND (c.item ->> 'ad_id')::bigint = :ad_id
                  AND d.decided_at >= GREATEST(
                        now() - make_interval(days => :retention_days),
                        COALESCE(CAST(:date_from AS timestamptz), '-infinity'))
                  AND d.decided_at <= COALESCE(CAST(:date_to AS timestamptz), 'infinity')
                  AND (CAST(:group AS text) IS NULL OR d.group_code = CAST(:group AS text))
                ORDER BY d.decided_at DESC
                LIMIT :limit
                """
            ),
            {
                "ad_tenant_id": ad_tenant_id,
                "ad_id": ad_id,
                "retention_days": DECISION_RETENTION_DAYS,
                "date_from": date_from,
                "date_to": date_to,
                "group": group,
                "limit": max(1, min(int(limit), 100)),
            },
        ).mappings()
        return [
            {
                "request_id": str(r["request_id"]),
                "decided_at": r["decided_at"],
                "group": r["group_code"],
                "rank": r["rank"],
            }
            for r in rows
        ]

    def _ads_by_id(self, ad_tenant_id: int, ad_ids: list[Any]) -> dict[int, dict[str, Any]]:
        ids = sorted({int(i) for i in ad_ids if i is not None})
        if not ids:
            return {}
        rows = self.session.execute(
            text(
                """
                SELECT a.ad_id, a.ad_key, c.objective
                FROM leo_ads.ad a
                LEFT JOIN leo_ads.campaign c
                       ON c.campaign_id = a.campaign_id AND c.tenant_id = a.tenant_id
                WHERE a.tenant_id = :t AND a.ad_id = ANY(:ids)
                """
            ),
            {"t": ad_tenant_id, "ids": ids},
        ).mappings()
        return {int(r["ad_id"]): dict(r) for r in rows}

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------

    def comparison_report(
        self,
        ad_tenant_id: int,
        date_from: datetime,
        date_to: datetime,
        *,
        placement_key: Optional[str] = None,
        min_sample: int = DEFAULT_MIN_SAMPLE,
    ) -> dict[str, Any]:
        """Personalised versus hold-out delivery from the hourly statistics."""
        rows = self.session.execute(
            text(
                """
                SELECT s.group_code,
                       COALESCE(NULLIF(lower(btrim(c.objective)), ''), 'unspecified') AS objective,
                       SUM(s.impressions)::bigint AS impressions,
                       SUM(s.views)::bigint       AS views,
                       SUM(s.clicks)::bigint      AS clicks,
                       SUM(s.conversions)::bigint AS conversions
                FROM leo_ads.ad_stat_hourly s
                LEFT JOIN leo_ads.campaign c
                       ON c.campaign_id = s.campaign_id AND c.tenant_id = s.tenant_id
                LEFT JOIN leo_ads.placement p
                       ON p.placement_id = s.placement_id AND p.tenant_id = s.tenant_id
                WHERE s.tenant_id = :ad_tenant_id
                  AND s.hour_start >= :date_from AND s.hour_start < :date_to
                  AND (CAST(:placement_key AS text) IS NULL OR p.placement_key = CAST(:placement_key AS text))
                GROUP BY s.group_code, 2
                """
            ),
            {
                "ad_tenant_id": ad_tenant_id,
                "date_from": date_from,
                "date_to": date_to,
                "placement_key": placement_key,
            },
        ).mappings()
        return build_comparison(
            [dict(r) for r in rows], date_from, date_to, placement_key, min_sample
        )

    # ------------------------------------------------------------------
    # Erasure requests
    # ------------------------------------------------------------------

    def profile_keys(self, ad_tenant_id: int, profile_ref: str) -> list[bytes]:
        """Every visitor key linked to a master profile (empty when the reference is unknown)."""
        rows = self.session.execute(
            text(
                """
                SELECT user_key_hash
                FROM leo_ads.user_serving_key
                WHERE tenant_id = :ad_tenant_id AND profile_ref = :profile_ref
                ORDER BY user_key_hash
                """
            ),
            {"ad_tenant_id": ad_tenant_id, "profile_ref": profile_ref},
        ).scalars()
        return [bytes(r) for r in rows]

    def create_erasure_request(
        self,
        ad_tenant_id: int,
        user_key: bytes,
        *,
        urgent: bool,
        requested_by: Optional[str],
    ) -> dict[str, Any]:
        """Record the audit row for one visitor key (hashed; the raw visitor id is never stored)."""
        row = self.session.execute(
            text(
                f"""
                INSERT INTO leo_ads.erasure_request
                    (tenant_id, user_key_hash, urgent, requested_by)
                VALUES (:ad_tenant_id, :user_key, :urgent, :requested_by)
                RETURNING {_ERASURE_COLUMNS}
                """
            ),
            {
                "ad_tenant_id": ad_tenant_id,
                "user_key": bytes(user_key),
                "urgent": urgent,
                "requested_by": requested_by,
            },
        ).mappings().one()
        self.session.commit()
        return self._erasure(row)

    def mark_redis_purged(self, ad_tenant_id: int, erasure_id: int) -> Optional[dict[str, Any]]:
        """Move a pending request to ``redis_purged`` (a request already further along is left alone)."""
        row = self.session.execute(
            text(
                f"""
                UPDATE leo_ads.erasure_request
                SET status = 'redis_purged'
                WHERE tenant_id = :ad_tenant_id AND erasure_id = :erasure_id AND status = 'pending'
                RETURNING {_ERASURE_COLUMNS}
                """
            ),
            {"ad_tenant_id": ad_tenant_id, "erasure_id": erasure_id},
        ).mappings().one_or_none()
        self.session.commit()
        return self._erasure(row) if row is not None else self.get_erasure_request(ad_tenant_id, erasure_id)

    def get_erasure_request(self, ad_tenant_id: int, erasure_id: int) -> Optional[dict[str, Any]]:
        """One request of this tenant, or None (another tenant's id looks the same as an unknown one)."""
        row = self.session.execute(
            text(
                f"""
                SELECT {_ERASURE_COLUMNS}
                FROM leo_ads.erasure_request
                WHERE tenant_id = :ad_tenant_id AND erasure_id = :erasure_id
                """
            ),
            {"ad_tenant_id": ad_tenant_id, "erasure_id": erasure_id},
        ).mappings().one_or_none()
        return self._erasure(row) if row is not None else None

    @staticmethod
    def _erasure(row: Any) -> dict[str, Any]:
        return {
            "erasure_id": int(row["erasure_id"]),
            "status": row["status"],
            "urgent": bool(row["urgent"]),
            "requested_at": row["requested_at"],
            "completed_at": row["completed_at"],
            "deleted_counts": row["deleted_counts"],
        }

    @staticmethod
    def _setting(row: Any) -> dict[str, Any]:
        return {
            "scope_type": row["scope_type"],
            "scope_ref": row["scope_ref"],
            "enabled": bool(row["enabled"]),
            "holdout_pct": float(row["holdout_pct"]),
            "holdout_salt": row["holdout_salt"],
            # Consent mode only means something on the tenant scope.
            "consent_mode": row["consent_mode"] if row["scope_type"] == "tenant" else None,
            "updated_by": row["updated_by"],
            "updated_at": row["updated_at"],
        }
