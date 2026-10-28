"""Regression tests for full-demo metadata, helpers, and orchestration."""

import json
import random
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

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
        "reset_tenant_scoped_demo_tables", "fetch_event_profiles",
        "seed_campaign_experiments", "seed_campaign_performance_daily",
        "seed_relations", "seed_customer_contacts", "seed_transactions",
        "seed_graph_edges", "enrich_master_profiles", "fetch_master_profiles",
        "seed_persona_archetypes", "seed_customer_personas", "seed_content_items",
        "link_crm_contacts_to_master_profiles",
    ]
    returns = {
        "fetch_master_profiles": profiles,
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


@pytest.mark.parametrize("failure_stage", [None, "database", "s3", "statistics"])
def test_run_preserves_transaction_and_cleanup(monkeypatch, failure_stage):
    connection = MagicMock()
    seeder = seed.FullDemoSeeder(connection)
    result = seed.SeedResult(2, 2, 2)
    stages = {
        "database": MagicMock(return_value=(result, ["event-profile"])),
        "s3": MagicMock(return_value={"source": {"total_tracked_event": 1}}),
        "statistics": MagicMock(),
    }
    if failure_stage:
        stages[failure_stage].side_effect = RuntimeError(f"{failure_stage} failed")
    monkeypatch.setattr(seeder, "seed_database", stages["database"])
    monkeypatch.setattr(seed, "seed_behavioral_events", stages["s3"])
    monkeypatch.setattr(seed, "update_data_source_statistics", stages["statistics"])
    if failure_stage:
        with pytest.raises(RuntimeError, match=f"{failure_stage} failed"):
            seeder.run()
        connection.commit.assert_not_called()
        connection.rollback.assert_called_once()
    else:
        assert seeder.run() == result
        connection.commit.assert_called_once()
        connection.rollback.assert_not_called()
        stages["s3"].assert_called_once_with(["event-profile"])
    connection.close.assert_called_once()


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
