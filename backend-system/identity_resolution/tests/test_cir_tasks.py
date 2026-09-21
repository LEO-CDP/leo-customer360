"""Tests for bounded, Redis-coordinated CIR drain runs."""

from unittest.mock import MagicMock, call

from identity_resolution import cir_tasks


def test_identity_resolution_tasks_stops_at_batch_budget(monkeypatch):
    redis_client = MagicMock()
    redis_client.set.return_value = True
    redis_client.eval.return_value = 1
    connection = MagicMock()
    resolver = MagicMock()
    resolver.run_resolution_batch.side_effect = [2, 2, 2]

    monkeypatch.setattr(cir_tasks, "build_redis_client", lambda: redis_client)
    monkeypatch.setattr(cir_tasks.psycopg2, "connect", lambda **_kwargs: connection)
    monkeypatch.setattr(cir_tasks, "CustomerIdentityResolver", lambda **_kwargs: resolver)
    monkeypatch.setattr(cir_tasks, "BATCH_SIZE", 2)
    monkeypatch.setattr(cir_tasks, "MAX_BATCHES_PER_RUN", 2)

    assert cir_tasks.run_identity_resolution_tasks() == 4
    assert resolver.run_resolution_batch.call_count == 2
    assert redis_client.eval.call_count == 3


def test_identity_resolution_tasks_skips_when_another_worker_holds_lock(monkeypatch):
    redis_client = MagicMock()
    redis_client.set.return_value = False
    connect = MagicMock()

    monkeypatch.setattr(cir_tasks, "build_redis_client", lambda: redis_client)
    monkeypatch.setattr(cir_tasks.psycopg2, "connect", connect)

    assert cir_tasks.run_identity_resolution_tasks() == 0
    connect.assert_not_called()


def test_identity_resolution_tasks_projects_each_tenant_once(monkeypatch):
    redis_client = MagicMock()
    redis_client.set.return_value = True
    redis_client.eval.return_value = 1
    connection = MagicMock()
    resolver = MagicMock()
    projector = MagicMock()
    batches = iter(
        [
            (2, {"tenant-1": {"master-1"}}),
            (1, {"tenant-1": {"master-2"}, "tenant-2": {"master-3"}}),
        ]
    )

    def run_batch():
        processed, resolved_profiles = next(batches)
        resolver.last_resolved_profiles_by_tenant = resolved_profiles
        return processed

    resolver.run_resolution_batch.side_effect = run_batch

    monkeypatch.setattr(cir_tasks, "build_redis_client", lambda: redis_client)
    monkeypatch.setattr(cir_tasks.psycopg2, "connect", lambda **_kwargs: connection)
    monkeypatch.setattr(cir_tasks, "CustomerIdentityResolver", lambda **_kwargs: resolver)
    monkeypatch.setattr(cir_tasks, "MasterProfileEventProjector", lambda *_args, **_kwargs: projector)
    monkeypatch.setattr(cir_tasks, "BATCH_SIZE", 2)

    assert cir_tasks.run_identity_resolution_tasks() == 3
    assert projector.project_profiles.call_args_list == [
        call("tenant-1", {"master-1", "master-2"}),
        call("tenant-2", {"master-3"}),
    ]