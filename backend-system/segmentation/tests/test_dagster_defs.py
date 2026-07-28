"""Unit tests for dagster_defs (Dagster job/sensor wrapping the segmentation
recompute logic).

Runs the job fully in-process via Dagster's own executor (no real Postgres
needed) with segmentation.recompute functions mocked out, so these tests
verify the Dagster wiring itself (op -> job -> success/output, sensor cursor/
RunRequest/SkipReason behavior), not the recompute SQL itself (exercised
manually against a real database -- see docs/PLAN-SEGMENTS-API-IMPROVEMENT.md).
"""

from unittest.mock import MagicMock

import dagster_defs
from dagster import DagsterInstance, RunRequest, SkipReason, build_sensor_context


class TestSegmentationJob:
    def test_runs_successfully_and_returns_summary(self, monkeypatch):
        summary = {"segments_processed": 3, "segments_skipped": 1, "total_members": 42}
        monkeypatch.setattr(
            dagster_defs, "recompute_all_active_segments", MagicMock(return_value=summary)
        )

        result = dagster_defs.segmentation_job.execute_in_process()

        assert result.success
        assert result.output_for_node("recompute_segments_op") == summary
        dagster_defs.recompute_all_active_segments.assert_called_once_with()

    def test_definitions_expose_job_and_sensor(self):
        assert dagster_defs.defs.get_job_def("segmentation_job") is not None
        assert dagster_defs.defs.resolve_sensor_def("segmentation_poll_sensor") is not None


class TestSegmentationPollSensor:
    def test_requests_run_when_profiles_changed(self, monkeypatch):
        monkeypatch.setattr(
            dagster_defs, "count_recently_changed_master_profiles", MagicMock(return_value=5)
        )

        with DagsterInstance.ephemeral() as instance:
            context = build_sensor_context(instance=instance, cursor=None)
            result = dagster_defs.segmentation_poll_sensor(context)

        assert isinstance(result, RunRequest)
        assert context.cursor is not None

    def test_skips_when_nothing_changed(self, monkeypatch):
        monkeypatch.setattr(
            dagster_defs, "count_recently_changed_master_profiles", MagicMock(return_value=0)
        )

        with DagsterInstance.ephemeral() as instance:
            context = build_sensor_context(instance=instance, cursor=None)
            result = dagster_defs.segmentation_poll_sensor(context)

        assert isinstance(result, SkipReason)

    def test_skips_without_crashing_when_db_check_fails(self, monkeypatch):
        monkeypatch.setattr(
            dagster_defs,
            "count_recently_changed_master_profiles",
            MagicMock(side_effect=RuntimeError("connection refused")),
        )

        with DagsterInstance.ephemeral() as instance:
            context = build_sensor_context(instance=instance, cursor=None)
            result = dagster_defs.segmentation_poll_sensor(context)

        assert isinstance(result, SkipReason)
