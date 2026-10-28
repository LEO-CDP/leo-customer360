"""Wiring test: the ad_personalization code location loads and exposes its
definitions (mirrors the other code locations' ``test_dagster_defs.py``)."""

import dagster_defs


def test_definitions_load():
    assert dagster_defs.defs is not None


def test_partition_maintenance_job_runs_with_a_stubbed_database(monkeypatch):
    class FakeConnection:
        closed = False

        def close(self):
            self.closed = True

    connection = FakeConnection()
    summary = {"created": ["ad_event_2026_11"], "attached": [], "existing": 3,
               "would_drop": [], "dropped": []}
    seen = {}

    def fake_run_maintenance(conn, **kwargs):
        seen.update(kwargs, conn=conn)
        return summary

    monkeypatch.delenv("AD_PERSONALIZATION_PARTITION_DROP_ENABLED", raising=False)
    monkeypatch.setattr(dagster_defs, "connect", lambda: connection)
    monkeypatch.setattr(dagster_defs, "run_maintenance", fake_run_maintenance)

    result = dagster_defs.ad_partition_maintenance_job.execute_in_process()

    assert result.success
    assert result.output_for_node("partition_maintenance_op") == summary
    assert seen["conn"] is connection
    assert seen["schema"] == "leo_ads"
    assert seen["drop_enabled"] is False  # dropping stays off unless explicitly enabled
    assert connection.closed


def test_partition_maintenance_is_scheduled_daily():
    schedule = dagster_defs.ad_partition_maintenance_schedule
    assert schedule.job_name == "ad_partition_maintenance_job"
    assert schedule.cron_schedule == "0 2 * * *"


def test_every_scheduled_job_is_registered():
    """A schedule must never point at a job that is not in ``Definitions``."""
    repository = dagster_defs.defs.get_repository_def()
    job_names = {job.name for job in repository.get_all_jobs()}
    for schedule in repository.schedule_defs:
        assert schedule.job_name in job_names
