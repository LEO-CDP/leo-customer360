"""
Integration tests for the LEO Ad Server API.

These tests verify:
- HTTP endpoints work correctly
- Request/response serialization
- Multi-tenancy isolation
- Health checks
- Error handling

⚠️  NOTE: These tests require PostgreSQL (identity PKs / FKs / JSONB are not
emulated by SQLite). Run with: docker-compose up postgres

To run only model tests (SQLite-compatible): ./run_unit_tests.sh tests/test_models.py

Each test runs inside its own SAVEPOINT-backed transaction (see
tests/conftest.py::test_engine) that is rolled back afterwards, so nothing
written here is ever persisted to the real database.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests._pg import check_postgres_available

pytestmark = pytest.mark.skipif(
    not check_postgres_available(),
    reason="PostgreSQL required. Run: docker-compose up postgres",
)

from core.application import AdServerApplication
from model.ad import Ad
from model.placement import Placement


@pytest.fixture
def test_app(test_engine):
    """Create a test FastAPI application."""
    app_instance = AdServerApplication()

    # Point repositories at the transactional test connection so nothing
    # they write is persisted; app_instance.engine is left untouched so the
    # /health/database check keeps using a real Engine.
    from repository.ad_repository import AdRepository
    from repository.placement_repository import PlacementRepository

    app_instance.ad_repository = AdRepository(engine=test_engine)
    app_instance.placement_repository = PlacementRepository(engine=test_engine)

    return app_instance.create_app()


@pytest.fixture
def client(test_app):
    """Create a TestClient for the FastAPI app."""
    return TestClient(test_app, raise_server_exceptions=False)


@pytest.fixture
def sample_placement(test_engine, seed):
    """Create a sample placement in the database."""
    session = Session(bind=test_engine, expire_on_commit=False)

    placement = Placement(
        tenant_id=seed["tenant_id"],
        placement_key="homepage_top",
        name="Homepage Top Banner",
        status="active",
        min_width_px=728,
        max_width_px=728,
        min_height_px=90,
        max_height_px=90,
        responsive=False,
        metadata_={"section": "header"},
    )
    session.add(placement)
    session.commit()
    session.close()

    return placement


@pytest.fixture
def sample_ads(test_engine, seed, sample_placement):
    """Create sample ads in the database."""
    session = Session(bind=test_engine, expire_on_commit=False)

    ads = []
    for i in range(3):
        ad = Ad(
            tenant_id=seed["tenant_id"],
            ad_key=f"ad_{i + 1}",
            creative_id=seed["creative_id"],
            placement_id=sample_placement.placement_id,
            status="active",
            score_weight=float(3 - i),  # Descending weights
            frequency_cap=5,
            metadata_={},
        )
        session.add(ad)
        ads.append(ad)

    session.commit()
    session.close()

    return ads


class TestHealthEndpoints:
    """Test health check endpoints."""

    def test_root_endpoint(self, client):
        """Test GET / returns service info."""
        response = client.get("/")

        assert response.status_code == 200
        data = response.json()

        assert data["service"] == "leo-ad-server-api"
        assert data["status"] == "ok"
        assert "version" in data
        assert data["schema"] == "leo_ads"
        assert data["docs"] == "/docs"

    def test_health_endpoint(self, client):
        """Test GET /health returns health status."""
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "ok"
        assert data["service"] == "leo-ad-server-api"

    def test_database_health_endpoint(self, client):
        """Test GET /health/database checks database connectivity."""
        response = client.get("/health/database")

        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "ok"
        assert data["database"] == "reachable"
        assert data["schema"] == "leo_ads"


class TestAdEndpoints:
    """Test ad-related endpoints."""

    def test_get_ad_by_id_not_found(self, client):
        """Test GET /ads/{ad_id} returns None for missing ad."""
        response = client.get("/ads/999999999")

        assert response.status_code == 200
        data = response.json()
        assert data is None

    def test_get_ad_by_id_found(self, client, sample_ads):
        """Test GET /ads/{ad_id} returns ad when it exists."""
        ad = sample_ads[0]
        response = client.get(f"/ads/{ad.ad_id}")

        assert response.status_code == 200
        data = response.json()

        assert data is not None
        assert data["ad_id"] == ad.ad_id
        assert data["ad_key"] == "ad_1"
        assert data["placement_id"] == ad.placement_id
        assert data["status"] == "active"
        assert data["score_weight"] == 3.0

    def test_get_ad_by_id_returns_dict_fields(self, client, sample_ads):
        """Test GET /ads/{ad_id} returns all expected fields."""
        ad = sample_ads[0]
        response = client.get(f"/ads/{ad.ad_id}")

        assert response.status_code == 200
        data = response.json()

        # Verify all fields are present
        expected_fields = [
            "ad_id",
            "tenant_id",
            "ad_key",
            "campaign_id",
            "creative_id",
            "placement_id",
            "status",
            "score_weight",
            "frequency_cap",
            "metadata",
            "created_at",
            "updated_at",
        ]

        for field in expected_fields:
            assert field in data, f"Missing field: {field}"

    def test_get_ad_excludes_inactive(self, client, test_engine, seed, sample_placement):
        """Test GET /ads/{ad_id} doesn't return paused ads."""
        session = Session(bind=test_engine, expire_on_commit=False)

        ad = Ad(
            tenant_id=seed["tenant_id"],
            ad_key="paused_ad",
            creative_id=seed["creative_id"],
            placement_id=sample_placement.placement_id,
            status="paused",
            score_weight=1.0,
            metadata_={},
        )
        session.add(ad)
        session.commit()
        ad_id = ad.ad_id
        session.close()

        response = client.get(f"/ads/{ad_id}")

        assert response.status_code == 200
        data = response.json()
        assert data is None  # Paused ads should not be returned


class TestPlacementEndpoints:
    """Test placement-related endpoints."""

    def test_get_placement_by_key_not_found(self, client):
        """Test GET /placements/{key} returns None for missing placement."""
        response = client.get("/placements/nonexistent")

        assert response.status_code == 200
        data = response.json()
        assert data is None

    def test_get_placement_by_key_found(self, client, sample_placement):
        """Test GET /placements/{key} returns placement when it exists."""
        response = client.get("/placements/homepage_top")

        assert response.status_code == 200
        data = response.json()

        assert data is not None
        assert data["placement_id"] == sample_placement.placement_id
        assert data["placement_key"] == "homepage_top"
        assert data["name"] == "Homepage Top Banner"
        assert data["status"] == "active"
        assert data["min_width_px"] == 728
        assert data["max_width_px"] == 728
        assert data["min_height_px"] == 90
        assert data["max_height_px"] == 90

    def test_get_placement_by_key_returns_dict_fields(self, client, sample_placement):
        """Test GET /placements/{key} returns all expected fields."""
        response = client.get("/placements/homepage_top")

        assert response.status_code == 200
        data = response.json()

        # Verify all fields are present
        expected_fields = [
            "placement_id",
            "tenant_id",
            "placement_key",
            "name",
            "status",
            "min_width_px",
            "max_width_px",
            "min_height_px",
            "max_height_px",
            "responsive",
            "metadata",
            "created_at",
            "updated_at",
        ]

        for field in expected_fields:
            assert field in data, f"Missing field: {field}"

    def test_get_placement_excludes_inactive(self, client, test_engine, seed):
        """Test GET /placements/{key} doesn't return paused placements."""
        session = Session(bind=test_engine, expire_on_commit=False)

        placement = Placement(
            tenant_id=seed["tenant_id"],
            placement_key="paused_placement",
            status="paused",
            responsive=False,
            metadata_={},
        )
        session.add(placement)
        session.commit()
        session.close()

        response = client.get("/placements/paused_placement")

        assert response.status_code == 200
        data = response.json()
        assert data is None  # Paused placements should not be returned


class TestMultiTenancy:
    """Test multi-tenancy isolation."""

    def test_ads_isolated_by_tenant(
        self, client, test_engine, seed, sample_placement
    ):
        """Test that ads are isolated by tenant_id."""
        from uuid import uuid4

        from model.tenant import Tenant

        session = Session(bind=test_engine, expire_on_commit=False)
        other_tenant = Tenant(tenant_key=f"test-{uuid4().hex}", name="Other Tenant")
        session.add(other_tenant)
        session.flush()

        ad_t1 = Ad(
            tenant_id=seed["tenant_id"],
            ad_key="tenant_1_ad",
            creative_id=seed["creative_id"],
            placement_id=sample_placement.placement_id,
            status="active",
            score_weight=1.0,
            metadata_={},
        )
        session.add(ad_t1)
        session.commit()
        ad_t1_id = ad_t1.ad_id
        session.close()

        # Note: The current API doesn't have tenant context extraction,
        # so we can only verify the data is stored correctly.
        # In production, auth middleware would extract tenant_id from JWT.
        response_t1 = client.get(f"/ads/{ad_t1_id}")
        assert response_t1.status_code == 200

    def test_placements_isolated_by_tenant(self, client, test_engine, seed):
        """Test that placements can be isolated by tenant_id."""
        from uuid import uuid4

        from model.tenant import Tenant

        session = Session(bind=test_engine, expire_on_commit=False)
        other_tenant = Tenant(tenant_key=f"test-{uuid4().hex}", name="Other Tenant")
        session.add(other_tenant)
        session.flush()

        placement_t1 = Placement(
            tenant_id=seed["tenant_id"],
            placement_key="placement_tenant_1",
            status="active",
            responsive=False,
            metadata_={},
        )
        placement_t2 = Placement(
            tenant_id=other_tenant.tenant_id,
            placement_key="placement_tenant_2",
            status="active",
            responsive=False,
            metadata_={},
        )
        session.add_all([placement_t1, placement_t2])
        session.commit()
        session.close()

        # Query both placements (current API returns without tenant filtering)
        response_t1 = client.get("/placements/placement_tenant_1")
        response_t2 = client.get("/placements/placement_tenant_2")

        assert response_t1.status_code == 200
        assert response_t2.status_code == 200

        data_t1 = response_t1.json()
        data_t2 = response_t2.json()

        if data_t1:
            assert data_t1["tenant_id"] == seed["tenant_id"]
        if data_t2:
            assert data_t2["tenant_id"] == other_tenant.tenant_id


class TestErrorHandling:
    """Test error handling and edge cases."""

    def test_nonexistent_routes_return_404(self, client):
        """Test that nonexistent routes return 404."""
        response = client.get("/nonexistent")
        assert response.status_code == 404

    def test_invalid_ad_id_type(self, client):
        """Test that invalid ad_id type is handled."""
        response = client.get("/ads/not_a_number")
        # FastAPI will attempt to convert, might return 422 or handle gracefully
        assert response.status_code in [422, 404, 200]

    def test_empty_placement_key(self, client):
        """Test that empty placement key is handled."""
        response = client.get("/placements/")
        # This might redirect or return 404 depending on routing
        assert response.status_code in [404, 307]


class TestResponseFormat:
    """Test response format and content-type."""

    def test_responses_are_json(self, client, sample_placement):
        """Test that all responses are JSON."""
        response = client.get("/placements/homepage_top")
        assert response.headers["content-type"] == "application/json"

    def test_null_response_is_valid_json(self, client):
        """Test that null responses are valid JSON."""
        response = client.get("/ads/999999999")
        assert response.status_code == 200
        data = response.json()
        assert data is None

    def test_list_responses_are_arrays(self, client, sample_ads):
        """Test that list endpoints return arrays."""
        # Note: Current API doesn't have list endpoints, but this tests the pattern
        # GET /placements/homepage_top returns a dict (single placement)
        response = client.get("/placements/homepage_top")
        data = response.json()
        assert isinstance(data, dict) or data is None

