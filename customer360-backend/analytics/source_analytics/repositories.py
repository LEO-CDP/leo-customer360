"""PostgreSQL repositories for source discovery and summary writes."""

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