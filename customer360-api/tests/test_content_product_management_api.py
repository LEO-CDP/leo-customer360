import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import require_tenant
from core.database import get_db
from core.repositories.content_repository import ContentRepository
from core.routers.content_api import router

TENANT_ID = "11111111-1111-1111-1111-111111111111"
PRODUCT_ID = "22222222-2222-2222-2222-222222222222"
CONTENT_ID = "33333333-3333-3333-3333-333333333333"


def make_product(**overrides):
    row = {
        "product_item_id": PRODUCT_ID,
        "tenant_id": uuid.UUID(TENANT_ID),
        "content_item_id": uuid.UUID(CONTENT_ID),
        "domain": "retail",
        "item_type": "product",
        "product_type": "retail_product",
        "source_id": "shop-1",
        "source_type": "",
        "product_id_type": "SKU",
        "product_id": "sku-1",
        "keywords": ["books"],
        "ext_attributes": {"product_category": "book"},
        "original_price": Decimal("20.00"),
        "sale_price": Decimal("15.00"),
        "currency": "USD",
        "title": "Product title",
        "summary": "Product summary",
        "image_url": None,
        "cta_label": "View product",
        "cta_url": "https://example.com/product",
        "status_code": 1,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    row.update(overrides)
    return row


def make_client():
    app = FastAPI()
    app.include_router(router)
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_tenant] = lambda: TENANT_ID
    return TestClient(app), db


def test_product_list_is_tenant_scoped_and_returns_metadata():
    client, _db = make_client()
    repository = MagicMock()
    repository.list_products.return_value = [make_product()]
    with patch(
        "core.routers.content_api.ProductItemRepository",
        return_value=repository,
    ):
        response = client.get(
            "/content-items/products",
            params={"tenant_id": TENANT_ID, "domain": "retail", "q": "book"},
        )

    assert response.status_code == 200
    assert response.json()[0]["product_item_id"] == PRODUCT_ID
    assert response.json()[0]["source_id"] == "shop-1"
    assert response.json()[0]["ext_attributes"] == {"product_category": "book"}
    repository.list_products.assert_called_once_with(
        tenant_id=uuid.UUID(TENANT_ID),
        domain="retail",
        q="book",
        skip=0,
        limit=50,
    )


def test_product_list_rejects_a_different_tenant_filter():
    client, _db = make_client()

    response = client.get(
        "/content-items/products",
        params={"tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"},
    )

    assert response.status_code == 403


def test_content_listing_can_exclude_product_rows_for_the_contents_tab():
    client, _db = make_client()
    repository = MagicMock()
    repository.list_items.return_value = []
    with patch("core.routers.content_api.ContentRepository", return_value=repository):
        response = client.get("/content-items/", params={"content_only": "true"})

    assert response.status_code == 200
    assert repository.list_items.call_args.kwargs["tenant_id"] == uuid.UUID(TENANT_ID)
    assert repository.list_items.call_args.kwargs["content_only"] is True


def test_recommended_items_default_to_combined_segments_and_accept_segment_filter():
    client, _db = make_client()
    repository = MagicMock()
    recommended = make_product(
        matched_tags=["loyal"],
        segment_id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        agent_code="product_recommendation",
        rank=1,
        score=1.0,
        semantic_score=0.9,
        tag_score=1.0,
        strategy="hybrid",
        reason="segment_tag_overlap",
        generated_at=datetime.now(timezone.utc),
    )
    repository.get_recommended_items.return_value = [recommended]
    with patch("core.routers.content_api.ContentRepository", return_value=repository):
        response = client.get(
            "/content-items/recommended",
            params={"master_profile_id": PRODUCT_ID, "limit": 8},
        )

    assert response.status_code == 200
    assert response.json()[0]["item_type"] == "product"
    assert response.json()[0]["rank"] == 1
    repository.get_recommended_items.assert_called_once_with(
        tenant_id=uuid.UUID(TENANT_ID),
        master_profile_id=uuid.UUID(PRODUCT_ID),
        segment_id=None,
        item_type=None,
        limit=8,
    )

    repository.reset_mock()
    with patch("core.routers.content_api.ContentRepository", return_value=repository):
        response = client.get(
            "/content-items/recommended",
            params={
                "master_profile_id": PRODUCT_ID,
                "segment_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            },
        )

    assert response.status_code == 200
    assert repository.get_recommended_items.call_args.kwargs["segment_id"] == uuid.UUID(
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    )


@pytest.mark.parametrize("limit", [0, 51])
def test_recommended_items_rejects_out_of_range_limit(limit):
    client, _db = make_client()

    response = client.get(
        "/content-items/recommended",
        params={"master_profile_id": PRODUCT_ID, "limit": limit},
    )

    assert response.status_code == 422


def test_recommended_items_query_deduplicates_across_segments_and_scopes_tenant():
    session = MagicMock()
    rows_result = MagicMock()
    rows_result.mappings.return_value.all.return_value = [
        make_product(
            matched_tags=["loyal"],
            segment_id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            agent_code="product_recommendation",
            rank=1,
            score=1.0,
            semantic_score=0.9,
            tag_score=1.0,
            strategy="hybrid",
            reason="segment_tag_overlap",
            generated_at=datetime.now(timezone.utc),
        )
    ]
    session.execute.return_value = rows_result
    repository = ContentRepository(session)

    with patch(
        "leo_customer360_dao.repositories.content_repository.get_redis_client",
        return_value=None,
    ):
        result = repository.get_recommended_items(
            tenant_id=uuid.UUID(TENANT_ID),
            master_profile_id=uuid.UUID(PRODUCT_ID),
            limit=8,
        )

    assert len(result) == 1
    assert result[0]["content_item_id"] == uuid.UUID(CONTENT_ID)
    recommendations_query, query_params = session.execute.call_args.args
    assert "tenant_id = :tenant_id AND master_profile_id = :mpid" in str(
        recommendations_query
    )
    assert query_params == {
        "tenant_id": TENANT_ID,
        "mpid": PRODUCT_ID,
        "segment_id": None,
        "item_type": None,
        "limit": 8,
    }
    assert "DISTINCT ON (content_item_id)" in str(recommendations_query)
    assert "CAST(:segment_id AS uuid) IS NULL" in str(recommendations_query)
    assert "segment.segment_tag = ANY(profile.tags)" in str(recommendations_query)
    assert query_params["tenant_id"] == TENANT_ID
    assert query_params["segment_id"] is None


def test_create_product_validates_domain_and_creates_scoped_product():
    client, _db = make_client()
    repository = MagicMock()
    repository.create_product.return_value = make_product()
    payload = {
        "domain": "retail",
        "product_type": "retail_product",
        "source_id": "shop-1",
        "product_id_type": "SKU",
        "product_id": "sku-1",
        "title": "Product title",
        "summary": "Product summary",
        "cta_url": "https://example.com/product",
        "keywords": ["books"],
        "ext_attributes": {"product_category": "book"},
        "original_price": "20.00",
        "sale_price": "15.00",
        "currency": "USD",
    }
    with (
        patch("core.routers.content_api.validate_domain_value") as validate_domain,
        patch(
            "core.routers.content_api.ProductItemRepository",
            return_value=repository,
        ),
    ):
        response = client.post("/content-items/products", json=payload)

    assert response.status_code == 201
    assert response.json()["item_type"] == "product"
    assert response.json()["ext_attributes"] == {"product_category": "book"}
    validate_domain.assert_called_once()
    call = repository.create_product.call_args
    assert call.kwargs["tenant_id"] == uuid.UUID(TENANT_ID)
    assert call.kwargs["payload"].source_id == "shop-1"
    assert call.kwargs["payload"].ext_attributes == {"product_category": "book"}


def test_create_product_rejects_mismatched_domain_product_type_before_database_write():
    client, _db = make_client()
    with patch("core.routers.content_api.ProductItemRepository") as repository:
        response = client.post(
            "/content-items/products",
            json={
                "domain": "retail",
                "product_type": "STOCK",
                "product_id_type": "SKU",
                "product_id": "sku-1",
                "title": "A product",
            },
        )

    assert response.status_code == 422
    repository.assert_not_called()
