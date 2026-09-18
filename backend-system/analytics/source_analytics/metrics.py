"""Polars-backed aggregation for analytics counters."""

from typing import Any, Mapping

import polars as pl


class AnalyticsMetrics:
    """Compute bounded analytics summaries without leaving Polars."""

    @staticmethod
    def daily_stats(daily: Mapping[Any, Any]) -> tuple[int, int]:
        if not daily:
            return 0, 0

        summary = (
            pl.LazyFrame(
                {
                    "day": list(daily.keys()),
                    "events": [str(value) for value in daily.values()],
                }
            )
            .with_columns(pl.col("events").cast(pl.Int64, strict=False).fill_null(0))
            .select(
                pl.col("day").n_unique().alias("days"),
                pl.col("events").sum().alias("total"),
            )
            .collect()
            .row(0)
        )
        return int(summary[0]), int(summary[1])

    @staticmethod
    def aggregate_source_results(results: list[dict[str, Any]]) -> dict[str, int]:
        if not results:
            return AnalyticsMetrics.empty_summary()

        frame = (
            pl.LazyFrame(results)
            .with_columns(
                [
                    pl.col("skipped_running")
                    .cast(pl.Boolean, strict=False)
                    .fill_null(False),
                    pl.col("objects_processed")
                    .cast(pl.Int64, strict=False)
                    .fill_null(0),
                    pl.col("events_added")
                    .cast(pl.Int64, strict=False)
                    .fill_null(0),
                ]
            )
        )
        summary = (
            frame.select(
                (~pl.col("skipped_running")).sum().alias("sources_processed"),
                pl.col("skipped_running").sum().alias("sources_skipped_running"),
                pl.col("objects_processed").sum().alias("objects_processed"),
                pl.col("events_added").sum().alias("events_added"),
            )
            .collect()
            .row(0)
        )
        return {
            "sources_processed": int(summary[0]),
            "sources_skipped_running": int(summary[1]),
            "objects_processed": int(summary[2]),
            "events_added": int(summary[3]),
            "sources_total": len(results),
        }

    @staticmethod
    def empty_summary() -> dict[str, int]:
        return {
            "sources_processed": 0,
            "sources_skipped_running": 0,
            "objects_processed": 0,
            "events_added": 0,
            "sources_total": 0,
        }