"""
Unit tests for repositories.

Tests verify:
- Repository query logic
- Serialization (_to_dict methods)
- Multi-tenancy filtering
- Edge cases (NULL values, empty results, limits)

⚠️  NOTE: These tests require PostgreSQL (identity PKs / FKs / JSONB are not
emulated by SQLite). Run with: docker-compose up postgres

To run only model tests (SQLite-compatible): ./run_unit_tests.sh tests/test_models.py

Each test runs inside its own SAVEPOINT-backed transaction (see
tests/conftest.py::test_engine) that is rolled back afterwards, so nothing
written here is ever persisted to the real database.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from model.ad import Ad
from model.placement import Placement
from repository.ad_repository import AdRepository
from repository.placement_repository import PlacementRepository
from tests._pg import check_postgres_available

pytestmark = pytest.mark.skipif(
    not check_postgres_available(),
    reason="PostgreSQL with leo_ads schema required",
)


@pytest.fixture
def ad_repo(test_engine):
    """Create an AdRepository instance."""
    return AdRepository(engine=test_engine)


@pytest.fixture
def placement_repo(test_engine):
    """Create a PlacementRepository instance."""
    return PlacementRepository(engine=test_engine)


def _make_placement(test_engine, seed, **overrides):
    """Insert a Placement row and return its generated placement_id."""
    session = Session(bind=test_engine, expire_on_commit=False)
    fields = {
        "tenant_id": seed["tenant_id"],
        "placement_key": f"placement-{overrides.pop('placement_key', 'default')}",
        "status": "active",
        "responsive": False,
        "metadata_": {},
    }
    fields.update(overrides)
    placement = Placement(**fields)
    session.add(placement)
    session.commit()
    placement_id = placement.placement_id
    session.close()
    return placement_id


def _make_ad(test_engine, seed, placement_id, **overrides):
    """Insert an Ad row and return the ORM instance (ids populated)."""
    session = Session(bind=test_engine, expire_on_commit=False)
    fields = {
        "tenant_id": seed["tenant_id"],
        "ad_key": "ad-default",
        "creative_id": seed["creative_id"],
        "placement_id": placement_id,
        "status": "active",
        "score_weight": 1.0,
        "metadata_": {},
    }
    fields.update(overrides)
    ad = Ad(**fields)
    session.add(ad)
    session.commit()
    session.close()
    return ad


def _tenant_key(test_engine, tenant_id):
    """Return the tenant_key for a tenant_id."""
    session = Session(bind=test_engine, expire_on_commit=False)
    tenant_key = session.execute(
        text("SELECT tenant_key FROM leo_ads.tenant WHERE tenant_id = :tenant_id"),
        {"tenant_id": tenant_id},
    ).scalar_one()
    session.close()
    return tenant_key


class TestAdRepository:
    """Test AdRepository query methods."""

    def test_get_by_id_not_found(self, ad_repo):
        """Test get_by_id returns None when ad doesn't exist."""
        result = ad_repo.get_by_id(999_999_999)
        assert result is None

    def test_get_by_id_returns_dict(self, ad_repo, test_engine, seed):
        """Test get_by_id returns a serialized dict."""
        placement_id = _make_placement(test_engine, seed, placement_key="p1")
        ad = _make_ad(test_engine, seed, placement_id, ad_key="test_ad_1")

        result = ad_repo.get_by_id(ad.ad_id)

        assert result is not None
        assert isinstance(result, dict)
        assert result["ad_id"] == ad.ad_id
        assert result["tenant_id"] == seed["tenant_id"]
        assert result["ad_key"] == "test_ad_1"
        assert result["creative_id"] == seed["creative_id"]
        assert result["placement_id"] == placement_id
        assert result["status"] == "active"
        assert result["score_weight"] == 1.0

    def test_get_by_id_excludes_inactive_ads(self, ad_repo, test_engine, seed):
        """Test get_by_id only returns active ads."""
        placement_id = _make_placement(test_engine, seed, placement_key="p2")
        active_ad = _make_ad(test_engine, seed, placement_id, ad_key="active")
        paused_ad = _make_ad(
            test_engine, seed, placement_id, ad_key="paused", status="paused"
        )

        result_active = ad_repo.get_by_id(active_ad.ad_id)
        assert result_active is not None
        assert result_active["status"] == "active"

        result_paused = ad_repo.get_by_id(paused_ad.ad_id)
        assert result_paused is None

    def test_get_active_by_placement_empty(self, ad_repo, test_engine, seed):
        """Test get_active_by_placement returns empty list when no ads found."""
        placement_id = _make_placement(test_engine, seed, placement_key="p3")
        result = ad_repo.get_active_by_placement(
            tenant_id=seed["tenant_id"],
            placement_id=placement_id,
        )
        assert result == []

    def test_get_serving_ads_returns_placement_ad(self, ad_repo, test_engine, seed):
        """Direct placement lookups should return the linked ad payload."""
        placement_id = _make_placement(test_engine, seed, placement_key="serve_direct")
        _make_ad(
            test_engine,
            seed,
            placement_id,
            ad_key="serve_direct_ad",
            score_weight=12.0,
        )

        tenant_key = _tenant_key(test_engine, seed["tenant_id"])
        result = ad_repo.get_serving_ads(
            tenant_key=tenant_key,
            placement_ref="placement-serve_direct",
        )

        assert len(result) == 1
        assert result[0]["adId"] == "serve_direct_ad"
        assert result[0]["adPlacementId"] == "placement-serve_direct"

    def test_get_serving_ads_uses_fallback_ad_for_empty_placement(
        self, ad_repo, test_engine, seed
    ):
        """Empty placements should resolve to a tenant fallback ad."""
        empty_placement_id = _make_placement(
            test_engine, seed, placement_key="serve_empty_fallback"
        )
        fallback_placement_id = _make_placement(
            test_engine, seed, placement_key="serve_fallback_source"
        )
        _make_ad(
            test_engine,
            seed,
            fallback_placement_id,
            ad_key="fallback_ad",
            score_weight=99.0,
        )

        tenant_key = _tenant_key(test_engine, seed["tenant_id"])
        result = ad_repo.get_serving_ads(
            tenant_key=tenant_key,
            placement_ref="placement-serve_empty_fallback",
        )

        assert len(result) == 1
        assert result[0]["adId"] == "fallback_ad"
        assert result[0]["adPlacementId"] == "placement-serve_empty_fallback"
        assert result[0]["placement"]["width"] == 300
        assert result[0]["placement"]["unit"] == "px"
        assert "responsive" not in result[0]["placement"]

        assert empty_placement_id is not None

    def test_get_active_by_placement_returns_list(self, ad_repo, test_engine, seed):
        """Test get_active_by_placement returns list of dicts."""
        placement_id = _make_placement(test_engine, seed, placement_key="p4")

        for i in range(3):
            _make_ad(
                test_engine,
                seed,
                placement_id,
                ad_key=f"ad_{i + 1}",
                score_weight=float(3 - i),  # Descending weights for ranking
            )

        result = ad_repo.get_active_by_placement(
            tenant_id=seed["tenant_id"],
            placement_id=placement_id,
        )

        assert len(result) == 3
        assert all(isinstance(ad, dict) for ad in result)

        # Verify ordering by score_weight DESC
        assert result[0]["score_weight"] == 3.0
        assert result[1]["score_weight"] == 2.0
        assert result[2]["score_weight"] == 1.0

    def test_get_active_by_placement_filters_by_tenant(
        self, ad_repo, test_engine, seed
    ):
        """Test get_active_by_placement respects tenant_id filter."""
        from uuid import uuid4

        from model.tenant import Tenant

        placement_id = _make_placement(test_engine, seed, placement_key="p5")

        session = Session(bind=test_engine, expire_on_commit=False)
        other_tenant = Tenant(tenant_key=f"test-{uuid4().hex}", name="Other Tenant")
        session.add(other_tenant)
        session.commit()
        other_tenant_id = other_tenant.tenant_id
        session.close()

        _make_ad(test_engine, seed, placement_id, ad_key="tenant_1_ad")
        _make_ad(
            test_engine,
            {"tenant_id": other_tenant_id, "creative_id": seed["creative_id"]},
            placement_id,
            ad_key="tenant_2_ad",
        )

        result_t1 = ad_repo.get_active_by_placement(
            tenant_id=seed["tenant_id"],
            placement_id=placement_id,
        )
        assert len(result_t1) == 1
        assert result_t1[0]["tenant_id"] == seed["tenant_id"]

        result_t2 = ad_repo.get_active_by_placement(
            tenant_id=other_tenant_id,
            placement_id=placement_id,
        )
        assert len(result_t2) == 1
        assert result_t2[0]["tenant_id"] == other_tenant_id

    def test_get_active_by_placement_filters_by_placement(
        self, ad_repo, test_engine, seed
    ):
        """Test get_active_by_placement only returns ads for specified placement."""
        placement_a = _make_placement(test_engine, seed, placement_key="p6a")
        placement_b = _make_placement(test_engine, seed, placement_key="p6b")

        _make_ad(test_engine, seed, placement_a, ad_key="ad_a")
        _make_ad(test_engine, seed, placement_b, ad_key="ad_b")

        result = ad_repo.get_active_by_placement(
            tenant_id=seed["tenant_id"],
            placement_id=placement_a,
        )
        assert len(result) == 1
        assert result[0]["placement_id"] == placement_a

    def test_get_active_by_placement_excludes_inactive(
        self, ad_repo, test_engine, seed
    ):
        """Test get_active_by_placement excludes inactive ads."""
        placement_id = _make_placement(test_engine, seed, placement_key="p7")

        _make_ad(test_engine, seed, placement_id, ad_key="active_ad")
        _make_ad(test_engine, seed, placement_id, ad_key="paused_ad", status="paused")

        result = ad_repo.get_active_by_placement(
            tenant_id=seed["tenant_id"],
            placement_id=placement_id,
        )
        assert len(result) == 1
        assert result[0]["status"] == "active"

    def test_get_active_by_placement_respects_limit(self, ad_repo, test_engine, seed):
        """Test get_active_by_placement respects limit parameter."""
        placement_id = _make_placement(test_engine, seed, placement_key="p8")

        for i in range(10):
            _make_ad(test_engine, seed, placement_id, ad_key=f"ad_{i + 1}")

        result = ad_repo.get_active_by_placement(
            tenant_id=seed["tenant_id"],
            placement_id=placement_id,
            limit=5,
        )
        assert len(result) == 5

    def test_get_active_by_placement_caps_limit_at_100(
        self, ad_repo, test_engine, seed
    ):
        """Test get_active_by_placement caps limit at 100."""
        placement_id = _make_placement(test_engine, seed, placement_key="p9")

        for i in range(110):
            _make_ad(test_engine, seed, placement_id, ad_key=f"ad_{i + 1}")

        result = ad_repo.get_active_by_placement(
            tenant_id=seed["tenant_id"],
            placement_id=placement_id,
            limit=200,
        )
        assert len(result) == 100

    def test_ad_to_dict_serializes_all_fields(self, ad_repo, test_engine, seed):
        """Test _to_dict serializes all Ad fields correctly."""
        placement_id = _make_placement(test_engine, seed, placement_key="p10")
        ad = _make_ad(
            test_engine,
            seed,
            placement_id,
            ad_key="test_key",
            score_weight=2.5,
            frequency_cap=5,
            metadata_={"custom_field": "custom_value"},
        )

        result = ad_repo.get_by_id(ad.ad_id)

        assert result["ad_id"] == ad.ad_id
        assert result["tenant_id"] == seed["tenant_id"]
        assert result["ad_key"] == "test_key"
        assert result["creative_id"] == seed["creative_id"]
        assert result["placement_id"] == placement_id
        assert result["status"] == "active"
        assert result["score_weight"] == 2.5
        assert result["frequency_cap"] == 5
        assert result["metadata"] == {"custom_field": "custom_value"}
        assert "created_at" in result
        assert "updated_at" in result


class TestPlacementRepository:
    """Test PlacementRepository query methods."""

    def test_get_active_by_key_not_found(self, placement_repo):
        """Test get_active_by_key returns None when placement doesn't exist."""
        result = placement_repo.get_active_by_key("nonexistent_placement")
        assert result is None

    def test_get_active_by_key_returns_dict(self, placement_repo, test_engine, seed):
        """Test get_active_by_key returns a serialized dict."""
        placement_id = _make_placement(
            test_engine,
            seed,
            placement_key="homepage_top",
            name="Homepage Top Banner",
            min_width_px=728,
            max_width_px=728,
            min_height_px=90,
            max_height_px=90,
        )

        result = placement_repo.get_active_by_key("placement-homepage_top")

        assert result is not None
        assert isinstance(result, dict)
        assert result["placement_id"] == placement_id
        assert result["tenant_id"] == seed["tenant_id"]
        assert result["placement_key"] == "placement-homepage_top"
        assert result["name"] == "Homepage Top Banner"
        assert result["status"] == "active"
        assert result["min_width_px"] == 728
        assert result["max_width_px"] == 728

    def test_get_active_by_key_excludes_inactive(
        self, placement_repo, test_engine, seed
    ):
        """Test get_active_by_key only returns active placements."""
        _make_placement(
            test_engine, seed, placement_key="active_one", status="active"
        )
        _make_placement(
            test_engine, seed, placement_key="paused_one", status="paused"
        )

        result_active = placement_repo.get_active_by_key("placement-active_one")
        assert result_active is not None
        assert result_active["status"] == "active"

        result_paused = placement_repo.get_active_by_key("placement-paused_one")
        assert result_paused is None

    def test_get_active_by_key_with_tenant_filter(
        self, placement_repo, test_engine, seed
    ):
        """Test get_active_by_key respects optional tenant_id filter."""
        from uuid import uuid4

        from model.tenant import Tenant

        session = Session(bind=test_engine, expire_on_commit=False)
        other_tenant = Tenant(tenant_key=f"test-{uuid4().hex}", name="Other Tenant")
        session.add(other_tenant)
        session.commit()
        other_tenant_id = other_tenant.tenant_id
        session.close()

        _make_placement(test_engine, seed, placement_key="shared_key")
        _make_placement(
            test_engine, {"tenant_id": other_tenant_id}, placement_key="shared_key"
        )

        result_t1 = placement_repo.get_active_by_key(
            "placement-shared_key", tenant_id=seed["tenant_id"]
        )
        assert result_t1 is not None
        assert result_t1["tenant_id"] == seed["tenant_id"]

        result_t2 = placement_repo.get_active_by_key(
            "placement-shared_key", tenant_id=other_tenant_id
        )
        assert result_t2 is not None
        assert result_t2["tenant_id"] == other_tenant_id

    def test_placement_to_dict_serializes_all_fields(
        self, placement_repo, test_engine, seed
    ):
        """Test _to_dict serializes all Placement fields correctly."""
        placement_id = _make_placement(
            test_engine,
            seed,
            placement_key="test_placement",
            name="Test Placement",
            min_width_px=300,
            max_width_px=300,
            min_height_px=250,
            max_height_px=250,
            metadata_={"inventory_type": "sidebar"},
        )

        result = placement_repo.get_active_by_key("placement-test_placement")

        assert result["placement_id"] == placement_id
        assert result["tenant_id"] == seed["tenant_id"]
        assert result["placement_key"] == "placement-test_placement"
        assert result["name"] == "Test Placement"
        assert result["status"] == "active"
        assert result["min_width_px"] == 300
        assert result["max_width_px"] == 300
        assert result["min_height_px"] == 250
        assert result["max_height_px"] == 250
        assert result["responsive"] is False
        assert result["metadata"] == {"inventory_type": "sidebar"}
        assert "created_at" in result
        assert "updated_at" in result
