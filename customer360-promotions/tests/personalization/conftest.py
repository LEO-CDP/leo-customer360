"""
Fixtures for the ad personalisation tests.

Pure-logic tests (identity, hold-out, consent, scoring, ...) need nothing here
beyond ``hot_state`` and ``clock``. Tests that touch the database use the
transactional ``test_engine`` fixture from tests/conftest.py: everything written
is rolled back afterwards, and they are skipped when PostgreSQL with the leo_ads
schema (including personalization-schema.sql) is not reachable.

``two_tenants`` builds the setup behind the tenant-isolation tests: two tenants
that use the SAME placement key and ad key, so any query that forgets its tenant
filter returns the other tenant's rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

import pytest
from sqlalchemy import text

from personalization.hot_state import InMemoryHotState


class FakeClock:
    """A controllable clock for TTL and decay tests."""

    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def hot_state(clock: FakeClock) -> InMemoryHotState:
    """In-memory hot state. Set ``hot_state.available = False`` to simulate Redis down."""
    return InMemoryHotState(clock=clock)


@dataclass
class World:
    """One tenant with a placement and a creative, plus helpers to add ads."""

    connection: object
    tenant_id: int
    tenant_key: str
    placement_id: int
    placement_key: str
    creative_id: int

    def add_campaign(
        self,
        campaign_key: str,
        *,
        objective: Optional[str] = None,
        status: str = "active",
        starts_at: Optional[datetime] = None,
        ends_at: Optional[datetime] = None,
    ) -> int:
        return self.connection.execute(
            text(
                """
                INSERT INTO leo_ads.campaign
                    (tenant_id, campaign_key, name, objective, status, starts_at, ends_at)
                VALUES (:t, :key, :key, :objective, :status, :starts_at, :ends_at)
                RETURNING campaign_id
                """
            ),
            {
                "t": self.tenant_id,
                "key": campaign_key,
                "objective": objective,
                "status": status,
                "starts_at": starts_at,
                "ends_at": ends_at,
            },
        ).scalar_one()

    def add_ad(
        self,
        ad_key: str,
        *,
        status: str = "active",
        score_weight: float = 1.0,
        campaign_id: Optional[int] = None,
        frequency_cap: Optional[int] = None,
        creative_id: Optional[int] = None,
    ) -> int:
        return self.connection.execute(
            text(
                """
                INSERT INTO leo_ads.ad
                    (tenant_id, ad_key, campaign_id, creative_id, placement_id,
                     status, score_weight, frequency_cap)
                VALUES (:t, :key, :campaign_id, :creative_id, :placement_id,
                        :status, :score_weight, :frequency_cap)
                RETURNING ad_id
                """
            ),
            {
                "t": self.tenant_id,
                "key": ad_key,
                "campaign_id": campaign_id,
                "creative_id": creative_id or self.creative_id,
                "placement_id": self.placement_id,
                "status": status,
                "score_weight": score_weight,
                "frequency_cap": frequency_cap,
            },
        ).scalar_one()


@pytest.fixture
def world_factory(test_engine):
    """Create tenants (with a placement and creative) inside the test transaction."""

    def make(
        tenant_key: Optional[str] = None,
        placement_key: str = "homepage_top",
    ) -> World:
        key = tenant_key or f"test-{uuid4().hex[:12]}"
        tenant_id = test_engine.execute(
            text(
                "INSERT INTO leo_ads.tenant (tenant_key, name) VALUES (:k, :k) "
                "RETURNING tenant_id"
            ),
            {"k": key},
        ).scalar_one()
        placement_id = test_engine.execute(
            text(
                "INSERT INTO leo_ads.placement (tenant_id, placement_key, name) "
                "VALUES (:t, :k, :k) RETURNING placement_id"
            ),
            {"t": tenant_id, "k": placement_key},
        ).scalar_one()
        creative_id = test_engine.execute(
            text(
                "INSERT INTO leo_ads.creative "
                "(tenant_id, creative_key, ad_type, format_code) "
                "VALUES (:t, :k, 'display', '300x250') RETURNING creative_id"
            ),
            {"t": tenant_id, "k": f"creative-{uuid4().hex[:8]}"},
        ).scalar_one()
        return World(
            connection=test_engine,
            tenant_id=tenant_id,
            tenant_key=key,
            placement_id=placement_id,
            placement_key=placement_key,
            creative_id=creative_id,
        )

    return make


@pytest.fixture
def two_tenants(world_factory):
    """Two tenants sharing the same placement key and ad key (conflicting data)."""
    first = world_factory()
    second = world_factory()
    first.add_ad("ad_shared", score_weight=2.0)
    second.add_ad("ad_shared", score_weight=1.0)
    return first, second


# --------------------------------------------------------------------------
# HTTP-level fixtures (serve and events API tests)
# --------------------------------------------------------------------------

CONTRACT_PATH = (
    Path(__file__).resolve().parents[3]
    / "specs" / "003-ad-personalization-engine" / "contracts" / "data-plane.openapi.yaml"
)


@pytest.fixture(scope="session")
def contract_validator():
    """``validate(schema_name, instance)`` against the data-plane OpenAPI contract."""
    jsonschema = pytest.importorskip("jsonschema")
    yaml = pytest.importorskip("yaml")
    if not CONTRACT_PATH.exists():
        pytest.skip("data-plane contract not available in this checkout")
    components = yaml.safe_load(CONTRACT_PATH.read_text())["components"]

    def validate(schema_name: str, instance) -> None:
        schema = {"$ref": f"#/components/schemas/{schema_name}", "components": components}
        jsonschema.Draft202012Validator(schema).validate(instance)

    return validate


@pytest.fixture
def serve_app(test_engine, hot_state):
    """The real application with every repository on the rolled-back test transaction.

    Redis is the in-memory twin and the buffered writers are NOT started: tests
    call ``app.event_writer.flush()`` / ``app.decision_writer.flush()`` to land
    their rows inside the test transaction.
    """
    from core.application import PromotionsApplication
    from personalization.buffered_writer import BufferedWriter
    from personalization.decision_log import DecisionLog
    from personalization.settings import SettingsResolver
    from repository.ad_repository import AdRepository
    from repository.consent_repository import ConsentRepository
    from repository.event_repository import EventRepository
    from repository.placement_repository import PlacementRepository
    from repository.profile_repository import ProfileRepository
    from repository.setting_repository import SettingRepository
    from repository.stats_repository import StatsRepository
    from repository.tenant_repository import TenantRepository

    app = PromotionsApplication()
    app.hot_state = hot_state
    app.ad_repository = AdRepository(engine=test_engine)
    app.placement_repository = PlacementRepository(engine=test_engine)
    app.tenant_repository = TenantRepository(engine=test_engine)
    app.consent_repository = ConsentRepository(engine=test_engine)
    app.setting_repository = SettingRepository(engine=test_engine)
    app.settings_resolver = SettingsResolver(app.setting_repository, hot_state, 60)
    app.profile_repository = ProfileRepository(engine=test_engine)
    app.stats_repository = StatsRepository(engine=test_engine)
    app.event_repository = EventRepository(engine=test_engine)
    app.event_writer = BufferedWriter(
        app.event_repository.insert_events, max_size=1000, name="test-events", start=False
    )
    app.decision_writer = BufferedWriter(
        app.event_repository.insert_decisions, max_size=1000, name="test-decisions", start=False
    )
    app.decision_log = DecisionLog(app.decision_writer)
    return app


@pytest.fixture
def serve_client(serve_app):
    from fastapi.testclient import TestClient

    return TestClient(serve_app.create_app(), raise_server_exceptions=False)


@pytest.fixture
def enable_personalisation(test_engine):
    """Switch personalisation on for a tenant (tenant-scope row)."""

    def enable(world, *, consent_mode: str = "opt_out", holdout_pct: float = 0.0, enabled: bool = True):
        test_engine.execute(
            text(
                """
                INSERT INTO leo_ads.personalization_setting
                    (tenant_id, scope_type, scope_ref, enabled, holdout_pct, holdout_salt, consent_mode)
                VALUES (:t, 'tenant', '', :enabled, :holdout, 'test-salt', :mode)
                ON CONFLICT (tenant_id, scope_type, scope_ref) DO UPDATE SET
                    enabled = EXCLUDED.enabled, holdout_pct = EXCLUDED.holdout_pct,
                    consent_mode = EXCLUDED.consent_mode
                """
            ),
            {"t": world.tenant_id, "enabled": enabled, "holdout": holdout_pct, "mode": consent_mode},
        )

    return enable
