"""
Ad statistics repository.

Reads leo_ads.ad_stat_hourly, the hourly counts of unflagged events per ad and
group that the ad_stats Dagster job maintains. Persistence only: turning counts
into rates, baselines and scores is done by personalization/scoring.py.

Multi-tenancy:
    leo_ads has no row-level security. Every method takes a required ``tenant_id``
    and filters by it.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker

from personalization.scoring import AdStats

DEFAULT_WINDOW_DAYS = 14


class StatsRepository:
    """Read per-ad delivery statistics."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._session_factory = sessionmaker(
            bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
        )

    def get_ad_stats(
        self,
        tenant_id: int,
        ad_ids: list[int],
        window_days: int = DEFAULT_WINDOW_DAYS,
        timeout_ms: Optional[int] = None,
    ) -> dict[int, AdStats]:
        """Impressions, views, clicks and conversions per ad over the trailing window.

        Ads with no statistics are simply absent from the result. All groups
        (personalised, hold-out and default) are counted together. ``timeout_ms``
        bounds the query so a slow database cannot stall a serve request.
        """
        if not ad_ids:
            return {}

        with self._session_factory() as session:
            if timeout_ms:
                session.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
            rows = session.execute(
                text(
                    """
                    SELECT ad_id,
                           COALESCE(SUM(impressions), 0) AS impressions,
                           COALESCE(SUM(views), 0)       AS views,
                           COALESCE(SUM(clicks), 0)      AS clicks,
                           COALESCE(SUM(conversions), 0) AS conversions
                    FROM leo_ads.ad_stat_hourly
                    WHERE tenant_id = :tenant_id
                      AND ad_id = ANY(:ad_ids)
                      AND hour_start >= now() - make_interval(days => :days)
                    GROUP BY ad_id
                    """
                ),
                {"tenant_id": tenant_id, "ad_ids": list(ad_ids), "days": window_days},
            ).mappings().all()

        return {
            row["ad_id"]: AdStats(
                impressions=int(row["impressions"]),
                views=int(row["views"]),
                clicks=int(row["clicks"]),
                conversions=int(row["conversions"]),
            )
            for row in rows
        }

    def get_cohort_patterns(
        self,
        tenant_id: int,
        cohort_key: str,
        ad_ids: Optional[list[int]] = None,
        timeout_ms: Optional[int] = None,
    ) -> dict[int, dict[str, int]]:
        """Per-ad response of one cohort: ``{ad_id: {cohort_size, impressions, objective_events}}``.

        Rows exist only for cohorts the backend job found large enough to publish,
        so an absent cohort simply returns ``{}``.
        """
        with self._session_factory() as session:
            if timeout_ms:
                session.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
            rows = session.execute(
                text(
                    """
                    SELECT ad_id, cohort_size, impressions, objective_events
                    FROM leo_ads.cohort_pattern
                    WHERE tenant_id = :tenant_id
                      AND cohort_key = :cohort_key
                      AND (CAST(:ad_ids AS bigint[]) IS NULL OR ad_id = ANY(CAST(:ad_ids AS bigint[])))
                    """
                ),
                {"tenant_id": tenant_id, "cohort_key": cohort_key, "ad_ids": ad_ids},
            ).mappings().all()
        return {
            row["ad_id"]: {
                "cohort_size": int(row["cohort_size"]),
                "impressions": int(row["impressions"]),
                "objective_events": int(row["objective_events"]),
            }
            for row in rows
        }
