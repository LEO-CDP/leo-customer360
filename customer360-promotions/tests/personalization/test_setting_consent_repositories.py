"""
Repository tests for personalisation settings and consent.

These need PostgreSQL with the leo_ads schema and personalization-schema.sql
applied (see tests/conftest.py); they are skipped otherwise. Each test runs in a
rolled-back transaction, so nothing is persisted.
"""

import pytest
from sqlalchemy import text

from personalization.settings import SettingsResolver
from repository.consent_repository import ConsentRepository
from repository.setting_repository import SettingRepository, SettingValidationError
from tests._pg import check_postgres_available

pytestmark = pytest.mark.skipif(
    not check_postgres_available(),
    reason="PostgreSQL with leo_ads schema required",
)

KEY = b"\x01" * 16
OTHER_KEY = b"\x02" * 16


@pytest.fixture
def settings_repo(test_engine):
    return SettingRepository(engine=test_engine)


@pytest.fixture
def consent_repo(test_engine):
    return ConsentRepository(engine=test_engine)


def _has_table(connection, name):
    return connection.execute(
        text("SELECT to_regclass(:n)"), {"n": f"leo_ads.{name}"}
    ).scalar_one() is not None


@pytest.fixture(autouse=True)
def _require_personalisation_tables(test_engine):
    if not _has_table(test_engine, "personalization_setting"):
        pytest.skip("personalization-schema.sql has not been applied to this database")


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


def test_a_new_tenant_row_starts_off_with_the_documented_defaults(settings_repo, world_factory):
    world = world_factory()

    row = settings_repo.upsert(world.tenant_id, scope_type="tenant", updated_by="admin@example.com")

    assert row["enabled"] is False
    assert row["holdout_pct"] == 10.0
    assert row["consent_mode"] == "opt_in"
    assert row["holdout_salt"] == "default"
    assert row["scope_ref"] == ""
    assert row["updated_by"] == "admin@example.com"


def test_an_update_only_changes_the_fields_provided(settings_repo, world_factory):
    world = world_factory()
    settings_repo.upsert(world.tenant_id, scope_type="tenant", enabled=True, holdout_pct=20)

    row = settings_repo.upsert(world.tenant_id, scope_type="tenant", consent_mode="opt_out")

    assert row["enabled"] is True
    assert row["holdout_pct"] == 20.0
    assert row["consent_mode"] == "opt_out"


def test_a_new_measurement_period_replaces_the_salt(settings_repo, world_factory):
    world = world_factory()
    before = settings_repo.upsert(world.tenant_id, scope_type="tenant")

    after = settings_repo.upsert(
        world.tenant_id, scope_type="tenant", new_measurement_period=True
    )

    assert after["holdout_salt"] != before["holdout_salt"]
    assert after["holdout_salt"].isdigit()  # a UTC timestamp


def test_site_and_placement_rows_need_a_tenant_row_first(settings_repo, world_factory):
    world = world_factory()
    with pytest.raises(SettingValidationError):
        settings_repo.upsert(world.tenant_id, scope_type="placement", scope_ref="homepage_top")

    settings_repo.upsert(world.tenant_id, scope_type="tenant")
    row = settings_repo.upsert(
        world.tenant_id, scope_type="placement", scope_ref="homepage_top", enabled=False
    )
    assert (row["scope_type"], row["scope_ref"], row["enabled"]) == ("placement", "homepage_top", False)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"scope_type": "galaxy"},
        {"scope_type": "tenant", "scope_ref": "x"},
        {"scope_type": "site"},
        {"scope_type": "tenant", "holdout_pct": 101},
        {"scope_type": "tenant", "holdout_pct": -1},
        {"scope_type": "tenant", "consent_mode": "maybe"},
        {"scope_type": "placement", "scope_ref": "p", "consent_mode": "opt_out"},
        {"scope_type": "placement", "scope_ref": "p", "new_measurement_period": True},
    ],
)
def test_rule_violations_are_rejected(settings_repo, world_factory, kwargs):
    world = world_factory()
    settings_repo.upsert(world.tenant_id, scope_type="tenant")
    with pytest.raises(SettingValidationError):
        settings_repo.upsert(world.tenant_id, **kwargs)


def test_settings_are_isolated_between_tenants(settings_repo, world_factory):
    first, second = world_factory(), world_factory()
    settings_repo.upsert(first.tenant_id, scope_type="tenant", enabled=True, holdout_pct=30)
    settings_repo.upsert(second.tenant_id, scope_type="tenant", enabled=False, holdout_pct=5)

    assert [r["holdout_pct"] for r in settings_repo.list_for_tenant(first.tenant_id)] == [30.0]
    assert [r["holdout_pct"] for r in settings_repo.list_for_tenant(second.tenant_id)] == [5.0]
    assert settings_repo.get(first.tenant_id, "tenant", "")["enabled"] is True
    assert settings_repo.get(second.tenant_id, "tenant", "")["enabled"] is False
    assert settings_repo.list_for_tenant(first.tenant_id + 100_000) == []


def test_the_resolver_works_end_to_end_against_real_rows(settings_repo, world_factory, hot_state):
    world = world_factory()
    settings_repo.upsert(
        world.tenant_id, scope_type="tenant", enabled=True, holdout_pct=15, consent_mode="opt_out"
    )
    settings_repo.upsert(world.tenant_id, scope_type="placement", scope_ref="sidebar", enabled=False)
    resolver = SettingsResolver(settings_repo, hot_state, ttl_seconds=60)

    homepage = resolver.resolve(world.tenant_id, "homepage_top")
    sidebar = resolver.resolve(world.tenant_id, "sidebar")

    assert (homepage.enabled, homepage.holdout_pct, homepage.consent_mode) == (True, 15.0, "opt_out")
    assert sidebar.enabled is False


# --------------------------------------------------------------------------
# Consent
# --------------------------------------------------------------------------


def _history(connection, tenant_id, key):
    return connection.execute(
        text(
            "SELECT status FROM leo_ads.consent_history "
            "WHERE tenant_id = :t AND user_key_hash = :k ORDER BY history_id"
        ),
        {"t": tenant_id, "k": key},
    ).scalars().all()


def test_no_record_means_no_status(consent_repo, world_factory):
    world = world_factory()
    assert consent_repo.get_status(world.tenant_id, KEY) is None


def test_recording_a_choice_is_idempotent_and_audited(consent_repo, world_factory, test_engine):
    world = world_factory()

    assert consent_repo.record(world.tenant_id, KEY, "granted") is True
    assert consent_repo.record(world.tenant_id, KEY, "granted") is False  # unchanged
    assert consent_repo.get_status(world.tenant_id, KEY) == "granted"
    assert _history(test_engine, world.tenant_id, KEY) == ["granted"]

    assert consent_repo.record(world.tenant_id, KEY, "denied", source="admin") is True
    assert consent_repo.get_status(world.tenant_id, KEY) == "denied"
    assert _history(test_engine, world.tenant_id, KEY) == ["granted", "denied"]


def test_invalid_consent_input_is_rejected(consent_repo, world_factory):
    world = world_factory()
    with pytest.raises(ValueError):
        consent_repo.record(world.tenant_id, KEY, "unknown")
    with pytest.raises(ValueError):
        consent_repo.record(world.tenant_id, KEY, "granted", source="")
    with pytest.raises(ValueError):
        consent_repo.record(world.tenant_id, KEY, "granted", source="x" * 41)


def test_the_same_visitor_key_is_independent_in_each_tenant(consent_repo, world_factory):
    first, second = world_factory(), world_factory()
    consent_repo.record(first.tenant_id, KEY, "granted")

    assert consent_repo.get_status(first.tenant_id, KEY) == "granted"
    assert consent_repo.get_status(second.tenant_id, KEY) is None


def test_erasure_removes_the_record_and_history_for_that_visitor_only(
    consent_repo, world_factory, test_engine
):
    first, second = world_factory(), world_factory()
    consent_repo.record(first.tenant_id, KEY, "granted")
    consent_repo.record(first.tenant_id, KEY, "denied")
    consent_repo.record(first.tenant_id, OTHER_KEY, "granted")
    consent_repo.record(second.tenant_id, KEY, "granted")

    counts = consent_repo.delete_for_key(first.tenant_id, KEY)

    assert counts == {"consent_record": 1, "consent_history": 2}
    assert consent_repo.get_status(first.tenant_id, KEY) is None
    assert _history(test_engine, first.tenant_id, KEY) == []
    # Other visitors and other tenants are untouched.
    assert consent_repo.get_status(first.tenant_id, OTHER_KEY) == "granted"
    assert consent_repo.get_status(second.tenant_id, KEY) == "granted"


def test_erasing_a_visitor_with_no_data_is_a_harmless_no_op(consent_repo, world_factory):
    world = world_factory()
    assert consent_repo.delete_for_key(world.tenant_id, KEY) == {
        "consent_record": 0,
        "consent_history": 0,
    }
