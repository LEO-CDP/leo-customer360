"""Unit tests for the event Loader.

Hermetic: the Redis consumer logic runs against an in-test FakeRedis and the Dagster
job/sensor wiring runs in-process with ``run_loader_once`` mocked out, so no real Redis
or PostgreSQL is required. These verify the consumer plumbing (group create, batch read,
parse, ACK) and the Dagster wiring -- not the (team-owned, gated-off) SQL landing path.
"""

import json
from unittest.mock import MagicMock

import dagster_defs
from dagster import DagsterInstance, RunRequest, build_sensor_context
from event_loader import loader


class FakeRedis:
    """Minimal stand-in for the redis-py consumer-group API used by loader.read_batch."""

    def __init__(self, messages=None, group_exists=False):
        self.messages = messages or []          # list of (entry_id, {fields})
        self.group_exists = group_exists
        self.created_group = None
        self.acked = []

    def xgroup_create(self, name, groupname, id="0", mkstream=False):
        if self.group_exists:
            raise RuntimeError("BUSYGROUP Consumer Group name already exists")
        self.created_group = (name, groupname)

    def xautoclaim(self, name, groupname, consumername, min_idle_time, start_id="0-0", count=1):
        return ("0-0", [], [])  # nothing stuck-pending to reclaim in these tests

    def xreadgroup(self, groupname, consumername, streams, count=1, block=None):
        stream_key = next(iter(streams))
        batch, self.messages = self.messages[:count], self.messages[count:]
        return [(stream_key, batch)] if batch else []

    def xack(self, name, groupname, *ids):
        self.acked.extend(ids)
        return len(ids)


def _cfg(**over):
    base = dict(
        redis_host="127.0.0.1", redis_port=6580, redis_password=None, redis_db=0,
        stream_key="cdp:events:raw", group="loader", consumer="c1", batch=10, block_ms=0,
        min_idle_ms=60000, write_events=False, tenant_id=None, domain="retail",
        source_system="WebTracking", db_schema="customer360",
    )
    base.update(over)
    return loader.LoaderConfig(**base)


def test_ensure_group_is_idempotent_when_group_exists():
    client = FakeRedis(group_exists=True)
    loader.ensure_group(client, "cdp:events:raw", "loader")  # BUSYGROUP must be swallowed


def test_ensure_group_creates_stream_and_group_when_absent():
    client = FakeRedis(group_exists=False)
    loader.ensure_group(client, "cdp:events:raw", "loader")
    assert client.created_group == ("cdp:events:raw", "loader")


def test_parse_entry_decodes_the_json_payload():
    env = {"data_source_id": "ds-1", "received_at": "2026-08-25T00:00:00+00:00",
           "event": {"event_name": "page_view"}}
    parsed = loader.parse_entry({"data_source_id": "ds-1", "payload": json.dumps(env)})
    assert parsed["event"] == {"event_name": "page_view"}


def test_run_loader_once_consumes_and_acks_without_persisting(monkeypatch):
    monkeypatch.setattr(loader.LoaderConfig, "from_env", classmethod(lambda cls: _cfg()))
    payload = json.dumps({"data_source_id": "ds-1", "received_at": "t", "event": {"event_name": "click"}})
    client = FakeRedis(messages=[("1-0", {"payload": payload}), ("2-0", {"payload": payload})])

    consumed = loader.run_loader_once(client=client)

    assert consumed == 2
    assert client.acked == ["1-0", "2-0"]  # ACKed even though persistence is off


def test_run_loader_once_persists_when_enabled(monkeypatch):
    monkeypatch.setattr(
        loader.LoaderConfig, "from_env",
        classmethod(lambda cls: _cfg(write_events=True, tenant_id="11111111-1111-1111-1111-111111111111")),
    )
    payload = json.dumps({"data_source_id": "ds-1", "received_at": "t", "event": {"event_name": "purchase"}})
    client = FakeRedis(messages=[("1-0", {"payload": payload})])
    captured = {}

    def fake_persist(conn, cfg, envelopes):
        captured["n"] = len(envelopes)
        return len(envelopes)

    conn = MagicMock()
    monkeypatch.setattr(loader, "persist_batch", fake_persist)

    consumed = loader.run_loader_once(client=client, pg_conn_factory=lambda: conn)

    assert consumed == 1
    assert captured["n"] == 1
    conn.commit.assert_called_once()
    conn.close.assert_called_once()
    assert client.acked == ["1-0"]


class TestEventLoaderDagster:
    def test_job_runs_and_returns_consumed_count(self, monkeypatch):
        monkeypatch.setattr(dagster_defs, "run_loader_once", MagicMock(return_value=7))

        result = dagster_defs.event_loader_job.execute_in_process()

        assert result.success
        assert result.output_for_node("drain_event_stream_op") == 7

    def test_definitions_expose_job_and_sensor(self):
        assert dagster_defs.defs.get_job_def("event_loader_job") is not None
        assert dagster_defs.defs.resolve_sensor_def("event_loader_poll_sensor") is not None

    def test_poll_sensor_requests_a_run(self):
        with DagsterInstance.ephemeral() as instance:
            context = build_sensor_context(instance=instance, cursor=None)
            results = list(dagster_defs.event_loader_poll_sensor(context))
        assert any(isinstance(r, RunRequest) for r in results)
