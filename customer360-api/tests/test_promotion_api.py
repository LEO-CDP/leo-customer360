"""Tests for explicit tenant-scoped promotion conversion."""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import SecretStr

from core.database import get_db
from core.routers.promotion_api import router
from core.services.promotion_service import PromotionService
from core.services.promotion_config import promotion_settings

TENANT = uuid.UUID("11111111-1111-1111-1111-111111111111")
SOURCE = uuid.UUID("22222222-2222-2222-2222-222222222222")
TOKEN = "t" * 40


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: MagicMock()

    @app.middleware("http")
    async def identity(request: Request, call_next):
        request.state.tenant_id = str(TENANT)
        request.state.user = {"roles": ["tenant_admin"]}
        return await call_next(request)

    monkeypatch.setattr("core.auth.SSO_LOGIN", True)
    return TestClient(app)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(promotion_settings, "promotion_api_url", "https://promotions.example.test")
    monkeypatch.setattr(promotion_settings, "promotion_management_tokens", SecretStr(json.dumps({"demo": TOKEN})))
    monkeypatch.setattr(promotion_settings, "promotion_tenant_keys", json.dumps({str(TENANT): "demo"}))
    monkeypatch.setattr(promotion_settings, "promotion_allow_insecure_http", False)


def test_routes_require_admin_and_validate_format(client):
    with patch("core.routers.promotion_api.require_tenant_admin", side_effect=HTTPException(status_code=403)):
        assert client.put(f"/promotions/campaigns/{SOURCE}").status_code == 403
        assert client.get("/promotions/creative-options").status_code == 403
    assert client.put(f"/promotions/content/{SOURCE}", json={"ad_type": "", "format_code": "native"}).status_code == 422
    assert client.put(f"/promotions/content/{SOURCE}", json={"ad_type": "display", "format_code": "native", "tenant_id": str(TENANT)}).status_code == 422


def test_creative_options_are_nonempty_labeled_and_match_sample_catalog(client):
    response = client.get("/promotions/creative-options")
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"ad_types", "format_codes"}
    assert {option["value"] for option in data["ad_types"]} == {
        "display", "dynamic_retargeting", "sponsored_content", "native", "affiliate"
    }
    assert {option["value"] for option in data["format_codes"]} == {
        "single_banner", "product_carousel", "native", "native_product", "google_ads", "external_widget"
    }
    for options in data.values():
        assert len({option["value"] for option in options}) == len(options)
        assert all(option["label"] and len(option["value"]) <= 80 for option in options)


def test_conversion_api_still_accepts_provider_specific_values(client):
    with patch.object(PromotionService, "content", return_value={"creative_id": 7}) as convert:
        response = client.put(
            f"/promotions/content/{SOURCE}",
            json={"ad_type": "custom_provider", "format_code": "custom_format"},
        )
    assert response.status_code == 200
    convert.assert_called_once_with(TENANT, SOURCE, "custom_provider", "custom_format")


def test_product_uses_linked_content_identity(client):
    product = {"content_item_id": SOURCE}
    content = SimpleNamespace(title="Item", summary="Summary", image_url=None, cta_label=None,
                              cta_url="https://example.test", item_type="product", domain="retail", segment_tags=[])
    with (
        patch("core.services.promotion_service.ProductItemRepository") as products,
        patch("core.services.promotion_service.ContentRepository") as contents,
        patch.object(PromotionService, "_sync", return_value={"creative_id": 7}) as sync,
    ):
        products.return_value.get_product.return_value = product
        contents.return_value.get_item.return_value = content
        response = client.put("/promotions/products/sku-1", json={"ad_type": "display", "format_code": "native"})
    assert response.status_code == 200
    products.return_value.get_product.assert_called_once_with(tenant_id=TENANT, product_item_id="sku-1")
    assert sync.call_args.args[2] == SOURCE
    assert sync.call_args.args[3]["content_payload"]["customer360_content_item_id"] == str(SOURCE)


def test_product_without_content_cannot_create_duplicate_creative(client):
    with patch("core.services.promotion_service.ProductItemRepository") as products:
        products.return_value.get_product.return_value = {"content_item_id": None}
        response = client.put("/promotions/products/sku-1", json={"ad_type": "display", "format_code": "native"})
    assert response.status_code == 409


def test_campaign_fields_are_sent_from_the_tenant_scoped_source(client):
    campaign = SimpleNamespace(
        name="Campaign", objective="awareness", budget_amount=None, currency="USD",
        campaign_code="C1", start_date=None, end_date=None,
    )
    with (
        patch("core.services.promotion_service.CampaignDraftRepository") as campaigns,
        patch.object(PromotionService, "_sync", return_value={"campaign_id": 3}) as sync,
    ):
        campaigns.return_value.get_campaign.return_value = campaign
        response = client.put(f"/promotions/campaigns/{SOURCE}")
    assert response.status_code == 200
    campaigns.return_value.get_campaign.assert_called_once_with(TENANT, SOURCE)
    assert sync.call_args.args[3]["metadata"]["customer360_campaign_id"] == str(SOURCE)


def test_foreign_content_is_not_converted(client):
    with (
        patch("core.services.promotion_service.ContentRepository") as contents,
        patch.object(PromotionService, "_sync") as sync,
    ):
        contents.return_value.get_item.return_value = None
        response = client.put(f"/promotions/content/{SOURCE}", json={"ad_type": "display", "format_code": "native"})
    assert response.status_code == 404
    contents.return_value.get_item.assert_called_once_with(SOURCE, TENANT)
    sync.assert_not_called()


def test_authenticated_service_call_is_scoped_and_verifies_identity(configured):
    response = MagicMock(status_code=200)
    response.json.return_value = {"campaign_id": 5, "campaign_key": f"customer360:{SOURCE}"}
    with patch("core.services.promotion_service.httpx.Client") as client_class:
        client = client_class.return_value.__enter__.return_value
        client.put.return_value = response
        result = PromotionService(MagicMock())._sync(TENANT, "campaigns", SOURCE, {"name": "Test"})
    assert result["campaign_id"] == 5
    call = client.put.call_args
    assert call.args[0] == f"https://promotions.example.test/management/customer360/{TENANT}/campaigns/{SOURCE}"
    assert call.kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert client_class.call_args.kwargs["follow_redirects"] is False


@pytest.mark.parametrize("status,expected", [(401, 503), (403, 503), (409, 409), (422, 422), (500, 502)])
def test_upstream_errors_are_explicit_and_do_not_leak_details(configured, status, expected):
    response = MagicMock(status_code=status)
    with patch("core.services.promotion_service.httpx.Client") as client_class:
        client_class.return_value.__enter__.return_value.put.return_value = response
        with pytest.raises(HTTPException) as exc:
            PromotionService(MagicMock())._sync(TENANT, "campaigns", SOURCE, {})
    assert exc.value.status_code == expected


def test_bad_tenant_mapping_and_insecure_url_fail_closed(configured, monkeypatch):
    monkeypatch.setattr(promotion_settings, "promotion_tenant_keys", "{}")
    with pytest.raises(HTTPException) as exc:
        PromotionService._connection(TENANT)
    assert exc.value.status_code == 503
    monkeypatch.setattr(promotion_settings, "promotion_tenant_keys", json.dumps({str(TENANT): "demo"}))
    monkeypatch.setattr(promotion_settings, "promotion_api_url", "http://remote.example.test")
    with pytest.raises(HTTPException) as exc:
        PromotionService._connection(TENANT)
    assert exc.value.status_code == 503


def test_network_timeout_is_not_reported_as_success(configured):
    with patch("core.services.promotion_service.httpx.Client") as client_class:
        client_class.return_value.__enter__.return_value.put.side_effect = httpx.ConnectTimeout("timeout")
        with pytest.raises(HTTPException) as exc:
            PromotionService(MagicMock())._sync(TENANT, "campaigns", SOURCE, {})
    assert exc.value.status_code == 503


def test_wrong_upstream_identity_is_rejected(configured):
    response = MagicMock(status_code=200)
    response.json.return_value = {"campaign_id": 5, "campaign_key": "customer360:not-the-source"}
    with patch("core.services.promotion_service.httpx.Client") as client_class:
        client_class.return_value.__enter__.return_value.put.return_value = response
        with pytest.raises(HTTPException) as exc:
            PromotionService(MagicMock())._sync(TENANT, "campaigns", SOURCE, {})
    assert exc.value.status_code == 502
