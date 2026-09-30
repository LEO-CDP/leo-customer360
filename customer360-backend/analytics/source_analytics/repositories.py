"""PostgreSQL repositories for source discovery and campaign metrics."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional


class AnalyticsRepository:
    """Own tenant-scoped PostgreSQL queries for tracking-log analytics."""

    def __init__(self, db_schema: str) -> None:
        self.db_schema = db_schema

    @staticmethod
    def set_tenant_context(cursor: Any, tenant_id: Optional[str]) -> None:
        value = str(tenant_id).strip() if tenant_id is not None else ""
        cursor.execute("SET app.tenant_id = %s", (value,))

    def fetch_data_sources(
        self,
        connection: Any,
        limit: int,
    ) -> list[tuple[str, str]]:
        sources: list[tuple[str, str]] = []
        unlimited = limit <= 0
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT tenant_id FROM {self.db_schema}.sys_tenant ORDER BY tenant_id")
            tenant_ids = [str(row[0]) for row in cursor.fetchall()]
            for tenant_id in tenant_ids:
                self.set_tenant_context(cursor, tenant_id)
                query = f"""
                    SELECT data_source_id, tenant_id
                    FROM {self.db_schema}.sys_data_source
                    WHERE tenant_id = %s AND status = 1
                    ORDER BY data_source_id
                """
                params: tuple[Any, ...] = (tenant_id,)
                if not unlimited:
                    query += " LIMIT %s"
                    params = (tenant_id, limit)
                cursor.execute(query, params)
                sources.extend((str(row[0]), str(row[1])) for row in cursor.fetchall())

        sources.sort(key=lambda source: source[0])
        return sources if unlimited else sources[:limit]

    def fetch_event_catalog(self, connection: Any) -> frozenset[str]:
        """Load active governed event names used to validate raw S3 events."""
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT event_name
                FROM {self.db_schema}.cdp_event_catalog
                WHERE status = 'ACTIVE'
                ORDER BY display_order, event_name
                """
            )
            return frozenset(str(row[0]).strip().lower() for row in cursor.fetchall())

    def update_data_source_summary(
        self,
        connection: Any,
        tenant_id: str,
        data_source_id: str,
        total_tracked_event: int,
        avg_daily_event: int,
        avg_events_per_profile: float,
    ) -> None:
        if total_tracked_event < 0:
            raise ValueError("total tracked event cannot be negative")

        with connection.cursor() as cursor:
            self.set_tenant_context(cursor, tenant_id)
            cursor.execute(
                f"""
                UPDATE {self.db_schema}.sys_data_source
                SET total_tracked_event = %s,
                    avg_daily_event = %s,
                    avg_events_per_profile = %s,
                    updated_at = NOW()
                WHERE data_source_id = %s AND tenant_id = %s AND status = 1
                """,
                (
                    total_tracked_event,
                    avg_daily_event,
                    avg_events_per_profile,
                    data_source_id,
                    tenant_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    f"Active data source {data_source_id} was not found or is not accessible"
                )
        connection.commit()

    def upsert_campaign_performance_event(
        self,
        connection: Any,
        tenant_id: str,
        event: dict[str, Any],
    ) -> bool:
        """Aggregate one variant-attributed event into daily campaign metrics.

        Events without both campaign and variant attribution remain raw-profile
        events and are deliberately excluded from campaign performance totals.
        The variant-aware partial unique index makes retries idempotent at the
        daily aggregate level when the same object is replayed.
        """
        campaign_id = self._uuid_text(event.get("campaign_id"))
        variant_id = self._uuid_text(event.get("experiment_variant_id"))
        if campaign_id is None or variant_id is None:
            return False

        event_name = str(event.get("event_name") or "").strip().lower().replace("_", "-")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        report_date = self._event_date(event.get("event_time"))
        if report_date is None:
            return False

        spend = self._decimal_value(payload.get("spend") if "spend" in payload else event.get("spend"))
        revenue = self._decimal_value(
            payload.get("revenue_estimated")
            if "revenue_estimated" in payload
            else payload.get("revenue", payload.get("event_value", event.get("revenue_estimated")))
        )
        impressions = self._integer_value(payload.get("impressions", event.get("impressions")))
        clicks = self._integer_value(payload.get("clicks", event.get("clicks")))
        conversions = self._integer_value(payload.get("conversions", event.get("conversions")))
        if event_name in {"impression", "ad-impression", "view"} and impressions == 0:
            impressions = 1
        if event_name in {"click", "ad-click"} and clicks == 0:
            clicks = 1
        if event_name in {"conversion", "converted", "purchase", "order-completed"} and conversions == 0:
            conversions = 1
        if event_name in {"spend", "ad-spend"} and spend == Decimal("0"):
            spend = self._decimal_value(payload.get("event_value", event.get("event_value")))

        if not any((spend, impressions, clicks, conversions, revenue)):
            return False

        with connection.cursor() as context_cursor:
            self.set_tenant_context(context_cursor, tenant_id)
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT 1
                FROM {self.db_schema}.crm_campaign_experiment_variants variant
                JOIN {self.db_schema}.crm_campaign_experiments experiment
                  ON experiment.tenant_id = variant.tenant_id
                 AND experiment.experiment_id = variant.experiment_id
                WHERE variant.tenant_id = %s
                  AND variant.variant_id = %s
                  AND experiment.campaign_id = %s
                """,
                (tenant_id, variant_id, campaign_id),
            )
            if cursor.fetchone() is None:
                connection.rollback()
                return False
            cursor.execute(
                f"""
                INSERT INTO {self.db_schema}.crm_campaign_performance_daily (
                    tenant_id, campaign_id, report_date, experiment_variant_id,
                    spend, impressions, clicks, conversions, revenue_estimated
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, campaign_id, report_date, experiment_variant_id)
                    WHERE experiment_variant_id IS NOT NULL
                DO UPDATE SET
                    spend = {self.db_schema}.crm_campaign_performance_daily.spend + EXCLUDED.spend,
                    impressions = {self.db_schema}.crm_campaign_performance_daily.impressions + EXCLUDED.impressions,
                    clicks = {self.db_schema}.crm_campaign_performance_daily.clicks + EXCLUDED.clicks,
                    conversions = {self.db_schema}.crm_campaign_performance_daily.conversions + EXCLUDED.conversions,
                    revenue_estimated = {self.db_schema}.crm_campaign_performance_daily.revenue_estimated + EXCLUDED.revenue_estimated,
                    updated_at = NOW()
                """,
                (tenant_id, campaign_id, report_date, variant_id, spend, impressions, clicks, conversions, revenue),
            )
        connection.commit()
        return True

    @staticmethod
    def _uuid_text(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            import uuid

            return str(uuid.UUID(text))
        except (ValueError, AttributeError, TypeError):
            return None

    @staticmethod
    def _event_date(value: Any) -> Optional[date]:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
            except ValueError:
                return None
        return None

    @staticmethod
    def _decimal_value(value: Any) -> Decimal:
        if value is None or isinstance(value, bool):
            return Decimal("0")
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return Decimal("0")

    @staticmethod
    def _integer_value(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0