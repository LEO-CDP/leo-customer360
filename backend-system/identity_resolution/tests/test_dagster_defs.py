"""Unit tests for dagster_defs (Dagster job/op wrapping the CIR daily job).

Runs the job fully in-process via Dagster's own executor (no real Postgres
needed) with `run_daily_identity_resolution` mocked out, so these tests
verify the Dagster wiring itself (op -> job -> success/output), not the CIR
business logic (already covered by tests/test_resolver.py etc.).
"""

from unittest.mock import MagicMock

import dagster_defs


class TestIdentityResolutionJob:
    def test_runs_successfully_and_returns_processed_count(self, monkeypatch):
        monkeypatch.setattr(
            dagster_defs, "run_daily_identity_resolution", MagicMock(return_value=7)
        )

        result = dagster_defs.identity_resolution_job.execute_in_process()

        assert result.success
        assert result.output_for_node("resolve_identities_op") == 7
        dagster_defs.run_daily_identity_resolution.assert_called_once_with()

    def test_zero_processed_is_still_a_successful_run(self, monkeypatch):
        monkeypatch.setattr(
            dagster_defs, "run_daily_identity_resolution", MagicMock(return_value=0)
        )

        result = dagster_defs.identity_resolution_job.execute_in_process()

        assert result.success
        assert result.output_for_node("resolve_identities_op") == 0

    def test_definitions_expose_job_and_sensor(self):
        assert dagster_defs.defs.get_job_def("identity_resolution_job") is not None
        assert (
            dagster_defs.defs.resolve_sensor_def("identity_resolution_poll_sensor")
            is not None
        )
