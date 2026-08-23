"""Unit tests for dagster_defs (Dagster job/sensor wrapping the segmentation
recompute logic).

Runs the job fully in-process via Dagster's own executor (no real Postgres
needed) with segmentation.recompute functions mocked out, so these tests
verify the Dagster wiring itself (op -> job -> success/output, sensor cursor/
RunRequest/SkipReason behavior), not the recompute SQL itself (exercised
manually against a real database -- see docs/PLAN-SEGMENTS-API-IMPROVEMENT.md).
"""

import logging
from unittest.mock import MagicMock

import dagster_defs
from dagster import DagsterInstance, RunRequest, SkipReason, build_sensor_context
from segmentation import recompute
from segmentation.rls import set_tenant_context


def test_sets_parameterized_tenant_context():
    cursor = MagicMock()

    set_tenant_context(cursor, "tenant-1")

    cursor.execute.assert_called_once_with("SET app.tenant_id = %s", ("tenant-1",))


class TestSegmentationJob:
    def test_runs_successfully_and_returns_summary(self, monkeypatch):
        summary = {"tenant_id": None, "segments_processed": 3, "segments_skipped": 1, "total_members": 42}
        monkeypatch.setattr(
            dagster_defs, "recompute_all_active_segments", MagicMock(return_value=summary)
        )

        result = dagster_defs.segmentation_job.execute_in_process()

        assert result.success
        assert result.output_for_node("recompute_segments_op") == summary
        dagster_defs.recompute_all_active_segments.assert_called_once()
        assert dagster_defs.recompute_all_active_segments.call_args.kwargs["tenant_id"] is None
        assert callable(dagster_defs.recompute_all_active_segments.call_args.kwargs["log"])

    def test_runs_scoped_to_tenant_when_run_config_provides_one(self, monkeypatch):
        tenant_id = "11111111-1111-1111-1111-111111111111"
        summary = {"tenant_id": tenant_id, "segments_processed": 1, "segments_skipped": 0, "total_members": 7}
        monkeypatch.setattr(
            dagster_defs, "recompute_all_active_segments", MagicMock(return_value=summary)
        )

        result = dagster_defs.segmentation_job.execute_in_process(
            run_config={"ops": {"recompute_segments_op": {"config": {"tenant_id": tenant_id}}}}
        )

        assert result.success
        assert result.output_for_node("recompute_segments_op") == summary
        dagster_defs.recompute_all_active_segments.assert_called_once()
        assert dagster_defs.recompute_all_active_segments.call_args.kwargs["tenant_id"] == tenant_id
        assert dagster_defs.recompute_all_active_segments.call_args.kwargs["segment_id"] is None
        assert callable(dagster_defs.recompute_all_active_segments.call_args.kwargs["log"])

    def test_runs_scoped_to_one_segment_when_run_config_provides_both_ids(self, monkeypatch):
        tenant_id = "11111111-1111-1111-1111-111111111111"
        segment_id = "22222222-2222-2222-2222-222222222222"
        summary = {"tenant_id": tenant_id, "segments_processed": 1, "segments_skipped": 0, "total_members": 7}
        monkeypatch.setattr(
            dagster_defs, "recompute_all_active_segments", MagicMock(return_value=summary)
        )

        result = dagster_defs.segmentation_job.execute_in_process(
            run_config={
                "ops": {
                    "recompute_segments_op": {
                        "config": {"tenant_id": tenant_id, "segment_id": segment_id}
                    }
                }
            }
        )

        assert result.success
        dagster_defs.recompute_all_active_segments.assert_called_once()
        assert dagster_defs.recompute_all_active_segments.call_args.kwargs["tenant_id"] == tenant_id
        assert dagster_defs.recompute_all_active_segments.call_args.kwargs["segment_id"] == segment_id

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


class TestSegmentationRecomputeLogging:
    def test_logs_member_count_for_each_recomputed_segment(self, monkeypatch, caplog):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [
            {
                "segment_id": "segment-1",
                "tenant_id": "tenant-1",
                "segment_tag": "segment_one",
                "sql_rules": "status_code = 1",
            }
        ]
        monkeypatch.setattr(recompute, "_connect", MagicMock(return_value=connection))
        monkeypatch.setattr(recompute, "_recompute_one_segment", MagicMock(return_value=7))
        caplog.set_level(logging.INFO, logger=recompute.logger.name)

        recompute.recompute_all_active_segments(tenant_id="tenant-1")

        assert "Recomputed segment segment-1 (tenant tenant-1): member_count=7" in caplog.text

    def test_selection_can_be_scoped_to_tenant_and_segment(self, monkeypatch):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        monkeypatch.setattr(recompute, "_connect", MagicMock(return_value=connection))

        recompute.recompute_all_active_segments(
            tenant_id="tenant-1",
            segment_id="segment-1",
        )

        select_sql, select_params = cursor.execute.call_args.args
        assert "AND tenant_id = %(tenant_id)s" in select_sql
        assert "AND segment_id = %(segment_id)s" in select_sql
        assert select_params == {"tenant_id": "tenant-1", "segment_id": "segment-1"}

    def test_segment_scope_requires_tenant_scope(self):
        try:
            recompute.recompute_all_active_segments(segment_id="segment-1")
        except ValueError as exc:
            assert str(exc) == "tenant_id is required when segment_id is provided"
        else:
            raise AssertionError("segment_id without tenant_id should be rejected")
