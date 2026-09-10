"""Tests for bounded, Redis-coordinated CIR drain runs."""

from unittest.mock import MagicMock

from identity_resolution import daily_job


def test_daily_run_stops_at_batch_budget(monkeypatch):
    redis_client = MagicMock()
    redis_client.set.return_value = True
    redis_client.eval.return_value = 1
    connection = MagicMock()
    resolver = MagicMock()
    resolver.run_resolution_batch.side_effect = [2, 2, 2]

    monkeypatch.setattr(daily_job, "build_redis_client", lambda: redis_client)
    monkeypatch.setattr(daily_job.psycopg2, "connect", lambda **_kwargs: connection)
    monkeypatch.setattr(daily_job, "CustomerIdentityResolver", lambda **_kwargs: resolver)
    monkeypatch.setattr(daily_job, "BATCH_SIZE", 2)
    monkeypatch.setattr(daily_job, "MAX_BATCHES_PER_RUN", 2)

    assert daily_job.run_daily_identity_resolution() == 4
    assert resolver.run_resolution_batch.call_count == 2
    assert redis_client.eval.call_count == 3


def test_daily_run_skips_when_another_worker_holds_lock(monkeypatch):
    redis_client = MagicMock()
    redis_client.set.return_value = False
    connect = MagicMock()

    monkeypatch.setattr(daily_job, "build_redis_client", lambda: redis_client)
    monkeypatch.setattr(daily_job.psycopg2, "connect", connect)

    assert daily_job.run_daily_identity_resolution() == 0
    connect.assert_not_called()