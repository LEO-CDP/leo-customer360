"""
PUT /consent: the contract's status codes, idempotence, isolation and limits.

Needs PostgreSQL with personalization-schema.sql applied (skipped otherwise).
The repositories are pointed at the rolled-back test transaction and the hot
state is the in-memory twin, so nothing is persisted and no Redis is touched.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from core.application import PromotionsApplication
from core.config import promotions_settings
from personalization.identity import visitor_key
from personalization.settings import SettingsResolver
from repository.consent_repository import ConsentRepository
from repository.setting_repository import SettingRepository
from repository.tenant_repository import TenantRepository
from tests._pg import check_postgres_available

pytestmark = pytest.mark.skipif(
    not check_postgres_available(),
    reason="PostgreSQL with leo_ads schema required",
)

VISITOR = "0f8f2e0a-7a5c-4a7d-9c43-2f1f1c0d9b11"


@pytest.fixture(autouse=True)
def _require_personalisation_tables(test_engine):
    exists = test_engine.execute(
        text("SELECT to_regclass('leo_ads.consent_record')")
    ).scalar_one()
    if exists is None:
        pytest.skip("personalization-schema.sql has not been applied to this database")


@pytest.fixture
def app_instance(test_engine, hot_state):
    instance = PromotionsApplication()
    instance.hot_state = hot_state
    instance._now = lambda: 1_000_000.0  # one fixed rate-limit window per test
    instance.tenant_repository = TenantRepository(engine=test_engine)
    instance.consent_repository = ConsentRepository(engine=test_engine)
    instance.setting_repository = SettingRepository(engine=test_engine)
    instance.settings_resolver = SettingsResolver(instance.setting_repository, hot_state, 60)
    return instance


@pytest.fixture
def client(app_instance):
    return TestClient(app_instance.create_app(), raise_server_exceptions=False)


@pytest.fixture
def consent_repo_for(test_engine):
    return ConsentRepository(engine=test_engine)


def _body(tenant_key, **overrides):
    body = {"tenant": tenant_key, "leovid": VISITOR, "status": "granted", "source": "widget"}
    body.update(overrides)
    return body


def test_recording_a_choice_returns_204_and_stores_it_against_the_hashed_key(
    client, world_factory, consent_repo_for
):
    world = world_factory()

    response = client.put("/consent", json=_body(world.tenant_key))

    assert response.status_code == 204
    assert response.content == b""
    key = visitor_key(world.tenant_id, VISITOR)
    assert consent_repo_for.get_status(world.tenant_id, key) == "granted"


def test_repeating_the_same_choice_is_a_204_no_op(client, world_factory, test_engine):
    world = world_factory()
    first = client.put("/consent", json=_body(world.tenant_key))
    second = client.put("/consent", json=_body(world.tenant_key))

    assert (first.status_code, second.status_code) == (204, 204)
    history = test_engine.execute(
        text("SELECT count(*) FROM leo_ads.consent_history WHERE tenant_id = :t"),
        {"t": world.tenant_id},
    ).scalar_one()
    assert history == 1


def test_changing_the_choice_updates_it_and_audits_the_change(
    client, world_factory, consent_repo_for, test_engine
):
    world = world_factory()
    client.put("/consent", json=_body(world.tenant_key, status="granted"))
    client.put("/consent", json=_body(world.tenant_key, status="denied", source="widget"))

    key = visitor_key(world.tenant_id, VISITOR)
    assert consent_repo_for.get_status(world.tenant_id, key) == "denied"
    history = test_engine.execute(
        text(
            "SELECT status FROM leo_ads.consent_history WHERE tenant_id = :t ORDER BY history_id"
        ),
        {"t": world.tenant_id},
    ).scalars().all()
    assert history == ["granted", "denied"]


def test_the_raw_visitor_id_is_never_stored(client, world_factory, test_engine):
    world = world_factory()
    client.put("/consent", json=_body(world.tenant_key))

    stored = test_engine.execute(
        text("SELECT user_key_hash FROM leo_ads.consent_record WHERE tenant_id = :t"),
        {"t": world.tenant_id},
    ).scalar_one()
    assert VISITOR.encode() not in bytes(stored)
    assert len(bytes(stored)) == 16


def test_an_unknown_tenant_is_a_404(client):
    response = client.put("/consent", json=_body("no-such-tenant"))
    assert response.status_code == 404


def test_a_paused_tenant_is_treated_as_unknown(client, world_factory, test_engine):
    world = world_factory()
    test_engine.execute(
        text("UPDATE leo_ads.tenant SET status = 'paused' WHERE tenant_id = :t"),
        {"t": world.tenant_id},
    )
    assert client.put("/consent", json=_body(world.tenant_key)).status_code == 404


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "unknown"},
        {"status": ""},
        {"leovid": ""},
        {"leovid": "x" * 129},
        {"tenant": ""},
        {"source": ""},
        {"source": "x" * 41},
    ],
)
def test_invalid_bodies_are_422(client, world_factory, overrides):
    world = world_factory()
    response = client.put("/consent", json=_body(world.tenant_key, **overrides))
    assert response.status_code == 422


def test_missing_fields_are_422(client):
    assert client.put("/consent", json={"tenant": "demo"}).status_code == 422


def test_a_blank_visitor_id_is_422_not_a_server_error(client, world_factory):
    world = world_factory()
    response = client.put("/consent", json=_body(world.tenant_key, leovid="   "))
    assert response.status_code == 422


def test_the_same_visitor_id_is_independent_in_each_tenant(client, world_factory, consent_repo_for):
    first, second = world_factory(), world_factory()
    client.put("/consent", json=_body(first.tenant_key, status="denied"))

    first_key = visitor_key(first.tenant_id, VISITOR)
    second_key = visitor_key(second.tenant_id, VISITOR)
    assert first_key != second_key
    assert consent_repo_for.get_status(first.tenant_id, first_key) == "denied"
    assert consent_repo_for.get_status(second.tenant_id, second_key) is None


def test_without_a_secret_outside_development_nothing_can_be_recorded(
    client, world_factory, monkeypatch
):
    world = world_factory()
    monkeypatch.setattr(promotions_settings, "environment", "production")
    monkeypatch.setattr(promotions_settings, "visitor_key_master_secret", None)

    response = client.put("/consent", json=_body(world.tenant_key))

    assert response.status_code == 503


def test_a_burst_of_changes_is_rate_limited_with_retry_after(client, world_factory, monkeypatch):
    world = world_factory()
    monkeypatch.setattr(promotions_settings, "consent_rate_limit_per_minute", 3)

    statuses = [
        client.put("/consent", json=_body(world.tenant_key, status=s)).status_code
        for s in ["granted", "denied", "granted", "denied", "granted"]
    ]

    assert statuses[:3] == [204, 204, 204]
    assert statuses[3:] == [429, 429]
    blocked = client.put("/consent", json=_body(world.tenant_key))
    assert blocked.headers["Retry-After"] == "60"


def test_the_rate_limit_resets_in_the_next_minute(client, app_instance, world_factory, monkeypatch):
    world = world_factory()
    monkeypatch.setattr(promotions_settings, "consent_rate_limit_per_minute", 1)

    assert client.put("/consent", json=_body(world.tenant_key)).status_code == 204
    assert client.put("/consent", json=_body(world.tenant_key)).status_code == 429

    app_instance._now = lambda: 1_000_060.0  # the next one-minute window
    assert client.put("/consent", json=_body(world.tenant_key)).status_code == 204


def test_the_rate_limit_is_per_visitor(client, world_factory, monkeypatch):
    world = world_factory()
    monkeypatch.setattr(promotions_settings, "consent_rate_limit_per_minute", 1)

    assert client.put("/consent", json=_body(world.tenant_key)).status_code == 204
    assert client.put("/consent", json=_body(world.tenant_key)).status_code == 429
    other = client.put("/consent", json=_body(world.tenant_key, leovid="another-visitor"))
    assert other.status_code == 204


def test_when_redis_is_down_consent_is_still_recorded(
    client, world_factory, hot_state, consent_repo_for
):
    world = world_factory()
    hot_state.available = False

    response = client.put("/consent", json=_body(world.tenant_key, status="denied"))

    assert response.status_code == 204  # the rate limiter fails open
    key = visitor_key(world.tenant_id, VISITOR)
    assert consent_repo_for.get_status(world.tenant_id, key) == "denied"


def test_existing_endpoints_are_unaffected(client):
    assert client.get("/health").json()["service"] == "customer360-promotions"
    assert "/consent" in {route.path for route in client.app.routes}
