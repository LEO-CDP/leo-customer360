"""Regression tests for full-demo metadata, helpers, and orchestration."""

import gzip
import io
import json
import random
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock
from collections import Counter
from types import SimpleNamespace
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from pydantic import ValidationError

SEEDING_DIR = Path(__file__).resolve().parent / "dev-backend-seeding"
sys.path.insert(0, str(SEEDING_DIR))

import seed_full_demo_data as seed  # noqa: E402
import seeding_content_items as content  # noqa: E402
import seeding_utils as utils  # noqa: E402


def test_metadata_catalogs_and_tuple_shapes_are_preserved():
    metadata = utils.load_demo_metadata()
    assert len(metadata.campaigns) == 8
    assert len(metadata.lifecycle_stages) == 8
    assert set(metadata.lifecycle_stages) == set(seed.JOURNEY_STAGES)
    assert {
        seed.infer_journey_stage_from_objective(campaign[5])
        for campaign in metadata.campaigns
    } == set(seed.JOURNEY_STAGES)
    assert len(metadata.icp_archetypes) == 14
    assert len(metadata.accounts) == 28
    assert len(metadata.ai_agent_models) == 5
    assert len(metadata.data_sources) == 3
    assert metadata.campaigns[0][1] == "TRAVEL-BOOK-GOOG-001"
    assert isinstance(metadata.campaigns[0], tuple)
    assert isinstance(metadata.domain_transaction_catalog["travel"][0][4], tuple)
    assert seed.seed_content_items is content.seed_content_items
    supported_domains = {
        "travel", "media", "hospitality", "retail", "real_estate", "healthcare",
        "education",
    }
    assert {item["domain"] for item in content.CONTENT_CATALOG} == supported_domains
    assert all(
        sum(item["domain"] == domain for item in content.CONTENT_CATALOG) >= 7
        for domain in supported_domains
    )
    assert len(content.CONTENT_CATALOG) == 49
    assert content.CONTENT_CATALOG_PATH.name == "seeding_demo_contents.json"
    assert set(metadata.behavioral_event_templates) == supported_domains


def test_campaign_experiment_prioritizes_existing_baseline_segments():
    cursor = MagicMock()
    cursor.fetchall.return_value = [{"segment_id": "first"}, {"segment_id": "second"}]
    seed.seed_campaign_experiments(cursor, {seed.CAMPAIGNS[0][0]: "campaign-id"})
    assert "segment_tag IN ('churn_risk', 'champions')" in cursor.execute.call_args_list[0].args[0]
    assert cursor.execute.call_args_list[2].args[1][5] == "first"
    assert cursor.execute.call_args_list[3].args[1][5] == "second"


def test_metadata_path_is_independent_of_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert utils.load_demo_metadata().industries == seed.INDUSTRIES


@pytest.mark.parametrize("change", ["missing", "unknown", "wrong_shape"])
def test_invalid_metadata_is_not_silently_defaulted(tmp_path, change):
    payload = json.loads(utils.METADATA_PATH.read_text())
    if change == "missing":
        del payload["campaigns"]
    elif change == "unknown":
        payload["campains"] = payload["campaigns"]
    else:
        payload["campaigns"][0] = ["incomplete"]
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValidationError):
        utils.load_demo_metadata(path)


def test_missing_metadata_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        utils.load_demo_metadata(tmp_path / "missing.json")


def test_stable_helpers_preserve_identity_and_random_isolation():
    state = random.getstate()
    assert utils.demo_id("crm_account:NexaLearn") == seed.demo_id("crm_account:NexaLearn")
    assert utils.stable_rng("profile-1").getstate() == utils.stable_rng("profile-1").getstate()
    assert random.getstate() == state
    rng = utils.stable_rng("profile-1")
    assert utils.build_global_profile_name(rng, seed.METADATA) == (
        utils.build_global_profile_name(utils.stable_rng("profile-1"), seed.METADATA)
    )
    assert utils.canonical_demo_domain(None) == "retail"
    assert utils.canonical_demo_domain("education") == "education"
    assert seed._profile_domain("profile-1", "banking") == "banking"
    assert utils.email_token(" Jane.. Doe ") == "jane.doe"


@pytest.mark.parametrize("days", [1, 4, 365])
def test_event_day_offsets_stay_within_requested_window(days):
    rng = utils.stable_rng("event-days")
    assert all(1 <= utils.realistic_event_days_ago(rng, days) <= days for _ in range(100))


def test_table_names_and_vector_inputs_are_validated():
    assert utils.table_name("customer360", "crm_account") == "customer360.crm_account"
    assert utils.table_name("", "crm_account") == "crm_account"
    with pytest.raises(ValueError, match="Invalid SQL identifier"):
        utils.table_name("customer360;DROP", "crm_account")
    with pytest.raises(ValueError, match="must not be empty"):
        utils.table_name("customer360", "")
    assert utils.cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert utils.cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
    with pytest.raises(ValueError, match="equal dimensions"):
        utils.cosine_similarity([1.0], [1.0, 2.0])


def test_data_source_environment_overrides_are_applied(monkeypatch):
    metadata = utils.load_demo_metadata()
    monkeypatch.setenv("C360_TRACKER_DATA_SOURCE_URL", " https://tracker.example.test/logs ")
    monkeypatch.setenv("C360_TRACKER_DATA_SOURCE_THUMBNAIL_URL", "https://example.test/image.png")
    monkeypatch.delenv("C360_TRACKER_DATA_SOURCE_HOSTS", raising=False)
    tracker = metadata.data_sources[2].runtime_values()
    assert tracker["data_source_url"] == "https://tracker.example.test/logs"
    assert tracker["data_source_hosts"] == ["tracker.example.test"]
    assert tracker["thumbnail_url"] == "https://example.test/image.png"
    assert "environment" not in tracker
    monkeypatch.setenv("C360_TRACKER_DATA_SOURCE_HOSTS", " first.example.test, second.example.test ")
    assert metadata.data_sources[2].runtime_values()["data_source_hosts"] == [
        "first.example.test", "second.example.test",
    ]
    monkeypatch.setenv("C360_TRACKER_DATA_SOURCE_URL", "")
    assert metadata.data_sources[2].runtime_values()["data_source_url"] is None


def test_qr_configuration_and_event_partition_contract(monkeypatch):
    monkeypatch.setenv("C360_GA4_DATA_SOURCE_URL", "https://analytics.example.test/report?view=1")
    source = seed.METADATA.data_sources[1].runtime_values()
    qr = source["qr_code_data"]
    assert qr["target_url"] == source["data_source_url"]
    assert "&utm_source=google-analytics-4" in qr["tracking_url"]
    clock = datetime(2026, 10, 2, 19, 30, tzinfo=timezone(timedelta(hours=7)))
    assert utils.behavioral_event_hour(clock) == "2026-10-02-12"
    assert utils.behavioral_object_key("source-1", "2026-10-02-12") == (
        "events/2026-10-02-12/demo-behavioral-source-1.jsonl.gz"
    )


def test_database_stages_preserve_order_and_detail_limit(monkeypatch):
    cursor = MagicMock()
    profiles = [{"master_profile_id": str(index)} for index in range(65)]
    order = []
    crm = {"campaign": {"demo": "campaign-1"}}
    archetypes = {"travel": []}
    events = [{"raw_profile_id": "raw-1"}]
    steps = [
        "set_tenant_context", "fetch_master_profiles", "seed_relation_types",
        "seed_crm_entities", "seed_data_sources", "seed_ai_agents",
        "reset_tenant_scoped_demo_tables",
        "seed_campaign_experiments", "seed_campaign_performance_daily",
        "seed_relations", "seed_customer_contacts", "seed_transactions",
        "seed_graph_edges", "enrich_master_profiles", "fetch_master_profiles", "fetch_event_profiles",
        "seed_persona_archetypes", "seed_customer_personas", "seed_content_items",
        "link_crm_contacts_to_master_profiles",
    ]
    returns = {
        "fetch_master_profiles": profiles,
        "seed_data_sources": {"adjust-mobile-attribution": "registered-id"},
        "seed_crm_entities": crm,
        "fetch_event_profiles": events,
        "seed_campaign_experiments": {},
        "seed_persona_archetypes": archetypes,
        "seed_customer_personas": 65,
    }
    mocks = {}
    for name in set(steps):
        def record(*args, step=name):
            order.append(step)
            return returns.get(step)
        mock = MagicMock(side_effect=record)
        mocks[name] = mock
        monkeypatch.setattr(seed, name, mock)

    result, actual_events = seed.FullDemoSeeder(MagicMock()).seed_database(cursor)
    assert order == steps
    assert result == seed.SeedResult(65, 60, 65)
    assert actual_events == events
    mocks["set_tenant_context"].assert_called_once_with(cursor, utils.DEMO_TENANT_ID)
    mocks["seed_relations"].assert_called_once_with(cursor, profiles[:60])
    mocks["seed_customer_personas"].assert_called_once_with(cursor, profiles, archetypes)


def test_empty_profile_prerequisite_fails_before_writes(monkeypatch):
    monkeypatch.setattr(seed, "fetch_master_profiles", lambda cursor: [])
    write = MagicMock()
    monkeypatch.setattr(seed, "seed_relation_types", write)
    with pytest.raises(RuntimeError, match="No resolved master profiles"):
        seed.FullDemoSeeder(MagicMock()).seed_database(MagicMock())
    write.assert_not_called()


@pytest.mark.parametrize("failure_stage", [None, "database", "s3", "statistics", "profile_statistics"])
def test_run_preserves_transaction_and_cleanup(monkeypatch, failure_stage):
    connection = MagicMock()
    seeder = seed.FullDemoSeeder(connection)
    seeder.source_ids = {"adjust-mobile-attribution": "registered-id"}
    seeder.expected_profiles = {"profile-1"}
    result = seed.SeedResult(2, 2, 2)
    stages = {
        "database": MagicMock(return_value=(result, ["event-profile"])),
        "s3": MagicMock(return_value=({"source": {"total_tracked_event": 1}}, {"profile-1": {}})),
        "statistics": MagicMock(),
        "profile_statistics": MagicMock(),
    }
    if failure_stage:
        stages[failure_stage].side_effect = RuntimeError(f"{failure_stage} failed")
    monkeypatch.setattr(seeder, "seed_database", stages["database"])
    monkeypatch.setattr(seed, "seed_behavioral_events", stages["s3"])
    monkeypatch.setattr(seed, "update_data_source_statistics", stages["statistics"])
    monkeypatch.setattr(seed, "update_profile_event_statistics", stages["profile_statistics"])
    if failure_stage:
        with pytest.raises(RuntimeError, match=f"{failure_stage} failed"):
            seeder.run()
        connection.commit.assert_not_called()
        connection.rollback.assert_called_once()
    else:
        assert seeder.run() == result
        connection.commit.assert_called_once()
        connection.rollback.assert_not_called()
        stages["s3"].assert_called_once_with(
            ["event-profile"],
            source_ids={"adjust-mobile-attribution": "registered-id"},
            expected_profiles={"profile-1"},
        )
    connection.close.assert_called_once()


def test_source_seeding_returns_registered_ids_on_slug_conflict():
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"data_source_id": "registered-adjust"},
        {"data_source_id": "registered-ga4"},
        {"data_source_id": "registered-tracker"},
    ]
    source_ids = seed.seed_data_sources(cursor)
    assert source_ids == {
        "adjust-mobile-attribution": "registered-adjust",
        "google-analytics-4": "registered-ga4",
        "c360-tracker": "registered-tracker",
    }
    assert all("RETURNING data_source_id" in call.args[0] for call in cursor.execute.call_args_list)


def test_behavioral_events_cover_every_profile_and_use_registered_source_ids():
    source_ids = {
        "adjust-mobile-attribution": "aaaaaaaa-0000-0000-0000-000000000001",
        "google-analytics-4": "aaaaaaaa-0000-0000-0000-000000000002",
        "c360-tracker": "aaaaaaaa-0000-0000-0000-000000000003",
    }
    profiles = [
        {
            "master_profile_id": f"profile-{index}",
            "raw_profile_id": f"raw-{index}",
            "domain": domain,
            "source_system": source,
            "device_id": f"device-{index}",
        }
        for index, (domain, source) in enumerate(
            [("banking", "Adjust"), ("education", "WebTracking"), ("media", "OneSignal")]
        )
    ]
    profiles.append({
        "master_profile_id": "profile-0",
        "raw_profile_id": "raw-extra",
        "domain": "banking",
        "source_system": "WebTracking",
        "device_id": "device-0",
    })
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = []
    sources, profile_stats = seed.seed_behavioral_events(
        profiles, source_ids=source_ids,
        expected_profiles={row["master_profile_id"] for row in profiles},
        event_count=1, s3_client=client,
    )
    written = []
    for call in client.put_object.call_args_list:
        if call.kwargs["Key"].endswith(".jsonl.gz"):
            events = [json.loads(line) for line in gzip.decompress(call.kwargs["Body"]).splitlines()]
            assert all(call.kwargs["Bucket"] == f"data-tracking-{event['data_source_id']}" for event in events)
            written.extend(events)
    assert len(written) == 3 * seed.MIN_EVENTS_PER_MASTER_PROFILE
    assert {event["raw_profile_id"] for event in written} == {row["raw_profile_id"] for row in profiles}
    counts = Counter(event["master_profile_id"] for event in written)
    assert set(counts) == {row["master_profile_id"] for row in profiles}
    assert set(counts.values()) == {seed.MIN_EVENTS_PER_MASTER_PROFILE}
    assert set(sources) == set(source_ids.values())
    assert sum(value["total_tracked_event"] for value in sources.values()) == len(written)
    assert all(
        sum(metrics["total_tracked_events"] for metrics in profile.values()) == seed.MIN_EVENTS_PER_MASTER_PROFILE
        for profile in profile_stats.values()
    )
    now = datetime.now(timezone.utc)
    assert all(now - timedelta(days=30) <= datetime.fromisoformat(event["event_time"]) <= now for event in written)
    assert all(
        datetime.fromisoformat(event["event_time"])
        >= now - timedelta(days=seed.BEHAVIORAL_EVENT_LOOKBACK_DAYS) + timedelta(minutes=55)
        for event in written
    )
    assert len({event["identity"]["session_id"] for event in written}) == 9

    from leo_customer360_dao.repositories.event_query_repository import EventQueryRepository

    objects = {
        (call.kwargs["Bucket"], call.kwargs["Key"]): call.kwargs["Body"]
        for call in client.put_object.call_args_list
        if call.kwargs["Key"].endswith(".jsonl.gz")
    }

    class Reader:
        def get_paginator(self, operation):
            assert operation == "list_objects_v2"
            return self

        def paginate(self, *, Bucket, Prefix):
            return [{"Contents": [
                {"Key": key} for bucket, key in objects
                if bucket == Bucket and key.startswith(Prefix)
            ]}]

        def get_object(self, *, Bucket, Key):
            return {"Body": io.BytesIO(objects[Bucket, Key])}

    db = MagicMock()
    db.execute.return_value.all.return_value = [(UUID(value),) for value in source_ids.values()]
    reader = EventQueryRepository(
        SimpleNamespace(event_s3_bucket=None, event_s3_prefix="events", event_query_max_days=30),
        s3_client=Reader(),
    )
    totals = reader.query_daily_totals(db, UUID(utils.DEMO_TENANT_ID), days=30)
    assert sum(row["total"] for row in totals) == len(written)
    visible = reader.query(db, UUID(utils.DEMO_TENANT_ID), days=30, limit=None)
    assert {row["master_profile_id"] for row in visible} == set(counts)


def test_behavioral_events_reject_unlinked_profiles_before_writing():
    client = MagicMock()
    with pytest.raises(RuntimeError, match="lack an active raw link"):
        seed.seed_behavioral_events(
            [{"master_profile_id": "linked"}],
            source_ids={},
            expected_profiles={"linked", "missing"},
            s3_client=client,
        )
    client.put_object.assert_not_called()


def test_demo_event_cleanup_removes_only_matching_objects_and_markers():
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [{
        "Contents": [
            {"Key": "events/2026-10-01-00/demo-behavioral-source-1.jsonl.gz"},
            {"Key": "events/2026-10-01-00/demo-behavioral-other.jsonl.gz"},
            {"Key": "events/2026-10-01-00/user-event.jsonl.gz"},
        ]
    }]
    client.delete_objects.return_value = {}
    seed._clear_demo_event_objects(client, "data-tracking-source-1", "source-1")
    key = "events/2026-10-01-00/demo-behavioral-source-1.jsonl.gz"
    marker = f"_processed/{uuid5(NAMESPACE_URL, f's3://data-tracking-source-1/{key}')}.json"
    assert client.delete_objects.call_args.kwargs["Delete"]["Objects"] == [
        {"Key": key}, {"Key": marker},
    ]


def test_demo_event_cleanup_reports_s3_delete_errors():
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [{
        "Contents": [{"Key": "events/hour/demo-behavioral-source-1.jsonl.gz"}]
    }]
    client.delete_objects.return_value = {"Errors": [{"Code": "AccessDenied"}]}
    with pytest.raises(RuntimeError, match="Could not clear prior demo event objects"):
        seed._clear_demo_event_objects(client, "data-tracking-source-1", "source-1")


def test_profile_event_statistics_updates_all_profiles_and_rejects_partial_updates(monkeypatch):
    cursor = MagicMock()
    cursor.rowcount = 1
    update = MagicMock()
    monkeypatch.setattr(seed, "execute_values", update)
    stats = {"profile-1": {"source-1": {"total_tracked_events": 11}}}
    seed.update_profile_event_statistics(cursor, stats)
    args = update.call_args.args
    assert "m.tenant_id = v.tenant_id" in args[1]
    assert args[2][0][0:2] == (utils.DEMO_TENANT_ID, "profile-1")
    assert args[2][0][2].adapted == stats["profile-1"]
    cursor.rowcount = 0
    with pytest.raises(RuntimeError, match="Could not update all demo profile"):
        seed.update_profile_event_statistics(cursor, stats)


def test_main_delegates_to_seeder(monkeypatch):
    connection = MagicMock()
    factory = MagicMock(return_value=connection)
    runner = MagicMock()
    monkeypatch.setattr(seed.psycopg2, "connect", factory)
    monkeypatch.setattr(seed, "FullDemoSeeder", runner)
    monkeypatch.setattr(seed.sys, "argv", ["seed_full_demo_data.py"])
    seed.main()
    runner.assert_called_once_with(connection)
    runner.return_value.run.assert_called_once()


def test_legacy_cli_guard_runs_from_outside_repository(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SEEDING_DIR / "seed_full_demo_data.py"), "--new-data"],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert "--new-data moved to customer360-seeding/seed_api_data.py" in result.stderr
    assert "Traceback" not in result.stderr


def test_copied_seed_bundle_loads_adjacent_json(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for filename in (
        "seed_full_demo_data.py", "seeding_utils.py",
        "seed_demo_metadata.json", "seeding_content_items.py",
        "seeding_demo_contents.json",
    ):
        shutil.copy2(SEEDING_DIR / filename, bundle / filename)
    result = subprocess.run(
        [sys.executable, str(bundle / "seed_full_demo_data.py"), "--new-data"],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert "--new-data moved" in result.stderr
    assert "Traceback" not in result.stderr
