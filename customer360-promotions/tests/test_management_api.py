"""Security and CRUD tests for the promotions management API."""

import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.application import PromotionsApplication
from core.config import promotions_settings
from tests._pg import check_postgres_available

TOKEN_A = "a" * 40
TOKEN_B = "b" * 40


@pytest.fixture
def auth_client(monkeypatch):
    """Provide an isolated client with a mocked tenant lookup."""
    monkeypatch.setattr(
        promotions_settings,
        "management_tokens",
        SecretStr(json.dumps({"tenant-a": TOKEN_A, "tenant-b": TOKEN_B})),
    )
    app = PromotionsApplication().create_app()
    session = MagicMock()
    session.scalar.return_value = 10
    with patch("core.management_auth.Session") as session_class:
        session_class.return_value.__enter__.return_value = session
        yield TestClient(app), session


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/management/campaigns", None),
        ("POST", "/management/campaigns", {"campaign_key": "test", "name": "Test"}),
        ("GET", "/management/campaigns/1", None),
        ("PUT", "/management/campaigns/1", {"campaign_key": "test", "name": "Test"}),
        ("DELETE", "/management/campaigns/1", None),
        ("GET", "/management/creatives", None),
        ("POST", "/management/creatives", {"creative_key": "test", "ad_type": "display", "format_code": "native"}),
        ("GET", "/management/creatives/1", None),
        ("PUT", "/management/creatives/1", {"creative_key": "test", "ad_type": "display", "format_code": "native"}),
        ("DELETE", "/management/creatives/1", None),
    ],
)
def test_all_management_operations_require_bearer_token(auth_client, method, path, body):
    client, session = auth_client
    response = client.request(method, path, json=body)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    session.scalar.assert_not_called()


def test_unknown_token_is_rejected_without_database_lookup(auth_client):
    client, session = auth_client
    response = client.get("/management/campaigns", headers={"Authorization": "Bearer " + "x" * 40})
    assert response.status_code == 401
    session.scalar.assert_not_called()


@pytest.mark.parametrize("mapping", ["{}", "not-json", '{"tenant":"short"}', json.dumps({"a": TOKEN_A, "b": TOKEN_A})])
def test_missing_or_invalid_token_configuration_fails_closed(auth_client, monkeypatch, mapping):
    client, session = auth_client
    monkeypatch.setattr(promotions_settings, "management_tokens", SecretStr(mapping))
    response = client.get("/management/campaigns", headers={"Authorization": "Bearer " + TOKEN_A})
    assert response.status_code == 503
    session.scalar.assert_not_called()


def test_paused_or_missing_tenant_is_forbidden(auth_client):
    client, session = auth_client
    session.scalar.return_value = None
    response = client.get("/management/campaigns", headers={"Authorization": "Bearer " + TOKEN_A})
    assert response.status_code == 403
    statement = session.scalar.call_args.args[0]
    assert "tenant_key" in str(statement) and "status" in str(statement)


def test_authenticated_routes_use_token_tenant_and_validate_requests(auth_client):
    client, _session = auth_client
    repo = MagicMock()
    repo.list.return_value = []
    with patch("core.management_api.repository", return_value=repo):
        response = client.get("/management/campaigns?tenant_id=20&limit=2", headers={"Authorization": "Bearer " + TOKEN_A})
    assert response.status_code == 200
    assert repo.list.call_args.args[1:] == (10, 0, 2)

    headers = {"Authorization": "Bearer " + TOKEN_A}
    assert client.get("/management/campaigns?limit=101", headers=headers).status_code == 422
    assert client.get("/management/campaigns?offset=9223372036854775808", headers=headers).status_code == 422
    assert client.get("/management/campaigns/0", headers=headers).status_code == 422
    assert client.post("/management/campaigns", headers=headers, json={"campaign_key": "c", "name": "N", "tenant_id": 20}).status_code == 422
    assert client.post("/management/campaigns", headers=headers, json={"campaign_key": "c", "name": "N", "status": "running"}).status_code == 422
    assert client.post("/management/creatives", headers=headers, json={"creative_key": "c", "ad_type": "display", "format_code": "native", "version_no": 0}).status_code == 422
    assert client.post("/management/creatives", headers=headers, json={"creative_key": "c", "ad_type": "display", "format_code": "native", "starts_at": "2026-01-02T00:00:00Z", "ends_at": "2026-01-01T00:00:00Z"}).status_code == 422


def test_openapi_documents_bearer_auth():
    schema = PromotionsApplication().create_app().openapi()
    assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    for path in ("/management/campaigns", "/management/campaigns/{campaign_id}", "/management/creatives", "/management/creatives/{creative_id}"):
        for operation in schema["paths"][path].values():
            assert {"HTTPBearer": []} in operation["security"]


@pytest.mark.skipif(not check_postgres_available(), reason="PostgreSQL with leo_ads schema required")
def test_campaign_creative_crud_tenant_isolation_and_reference_checks(test_engine, monkeypatch):
    """Exercise management writes against real PostgreSQL in a rolled-back transaction."""
    suffix = uuid4().hex
    keys = ("a-" + suffix, "b-" + suffix)
    with Session(test_engine) as session:
        tenant_ids = [
            session.execute(
                text("INSERT INTO leo_ads.tenant (tenant_key, name) VALUES (:key, :name) RETURNING tenant_id"),
                {"key": key, "name": key},
            ).scalar_one()
            for key in keys
        ]
        advertiser_id = session.execute(
            text("INSERT INTO leo_ads.advertiser (tenant_id, advertiser_key, name) VALUES (:tenant_id, :key, :name) RETURNING advertiser_id"),
            {"tenant_id": tenant_ids[1], "key": "other", "name": "Other"},
        ).scalar_one()
        source_account_id = session.execute(
            text("INSERT INTO leo_ads.source_account (tenant_id, source_type_code, provider_key) VALUES (:tenant_id, 'local', 'other') RETURNING source_account_id"),
            {"tenant_id": tenant_ids[1]},
        ).scalar_one()
        source_asset_id = session.execute(
            text("INSERT INTO leo_ads.source_asset (tenant_id, asset_type, external_asset_id) VALUES (:tenant_id, 'creative', 'other') RETURNING source_asset_id"),
            {"tenant_id": tenant_ids[1]},
        ).scalar_one()
        session.commit()

    test_engine.begin_nested()
    monkeypatch.setattr(promotions_settings, "management_tokens", SecretStr(json.dumps({keys[0]: TOKEN_A, keys[1]: TOKEN_B})))
    application = PromotionsApplication()
    application.engine = test_engine
    client = TestClient(application.create_app())
    a = {"Authorization": "Bearer " + TOKEN_A}
    b = {"Authorization": "Bearer " + TOKEN_B}

    campaign_payload = {"campaign_key": "spring", "name": "Spring campaign", "metadata": {"source": "test"}}
    response = client.post("/management/campaigns", headers=a, json=campaign_payload)
    assert response.status_code == 201, response.text
    campaign = response.json()
    assert campaign["tenant_id"] == tenant_ids[0]
    assert campaign["metadata"] == {"source": "test"}
    campaign_id = campaign["campaign_id"]
    assert client.get(f"/management/campaigns/{campaign_id}", headers=b).status_code == 404
    assert client.put(f"/management/campaigns/{campaign_id}", headers=b, json=campaign_payload).status_code == 404
    assert client.delete(f"/management/campaigns/{campaign_id}", headers=b).status_code == 404
    assert client.get("/management/campaigns", headers=b).json() == []
    assert client.post("/management/campaigns", headers=a, json={**campaign_payload, "advertiser_id": advertiser_id}).status_code == 422
    assert client.post("/management/campaigns", headers=a, json={**campaign_payload, "source_account_id": source_account_id}).status_code == 422

    creative_payload = {"creative_key": "banner", "ad_type": "display", "format_code": "single_banner", "campaign_id": campaign_id, "content_payload": {"badge": "New"}}
    assert client.post("/management/creatives", headers=b, json=creative_payload).status_code == 422
    assert client.post("/management/creatives", headers=a, json={**creative_payload, "source_asset_id": source_asset_id}).status_code == 422
    assert client.post("/management/creatives", headers=a, json={**creative_payload, "advertiser_id": advertiser_id}).status_code == 422
    assert client.post("/management/creatives", headers=a, json={**creative_payload, "render_type_code": "nonexistent"}).status_code == 422
    response = client.post("/management/creatives", headers=a, json=creative_payload)
    assert response.status_code == 201, response.text
    creative_id = response.json()["creative_id"]
    assert response.json()["content_payload"] == {"badge": "New"}
    assert client.get(f"/management/creatives/{creative_id}", headers=b).status_code == 404
    assert client.put(f"/management/creatives/{creative_id}", headers=b, json=creative_payload).status_code == 404
    assert client.delete(f"/management/creatives/{creative_id}", headers=b).status_code == 404
    assert client.delete(f"/management/campaigns/{campaign_id}", headers=a).status_code == 409
    response = client.put(f"/management/creatives/{creative_id}", headers=a, json={**creative_payload, "headline": "Updated"})
    assert response.status_code == 200 and response.json()["headline"] == "Updated"
    response = client.put(f"/management/campaigns/{campaign_id}", headers=a, json={**campaign_payload, "name": "Updated"})
    assert response.status_code == 200 and response.json()["name"] == "Updated"
    assert client.post("/management/campaigns", headers=a, json=campaign_payload).status_code == 409
    with Session(test_engine) as session:
        placement_id = session.execute(
            text("INSERT INTO leo_ads.placement (tenant_id, placement_key) VALUES (:tenant_id, 'test-placement') RETURNING placement_id"),
            {"tenant_id": tenant_ids[0]},
        ).scalar_one()
        ad_id = session.execute(
            text("INSERT INTO leo_ads.ad (tenant_id, ad_key, placement_id, creative_id, campaign_id) VALUES (:tenant_id, 'test-ad', :placement_id, :creative_id, :campaign_id) RETURNING ad_id"),
            {"tenant_id": tenant_ids[0], "placement_id": placement_id, "creative_id": creative_id, "campaign_id": campaign_id},
        ).scalar_one()
        session.commit()
    assert client.delete(f"/management/creatives/{creative_id}", headers=a).status_code == 409
    with Session(test_engine) as session:
        session.execute(text("DELETE FROM leo_ads.ad WHERE ad_id = :ad_id"), {"ad_id": ad_id})
        session.commit()
    assert client.delete(f"/management/creatives/{creative_id}", headers=a).status_code == 204
    assert client.delete(f"/management/campaigns/{campaign_id}", headers=a).status_code == 204
    with Session(test_engine) as session:
        session.execute(text("UPDATE leo_ads.tenant SET status = 'paused' WHERE tenant_id = :tenant_id"), {"tenant_id": tenant_ids[0]})
        session.commit()
    assert client.get("/management/campaigns", headers=a).status_code == 403


@pytest.mark.skipif(not check_postgres_available(), reason="PostgreSQL with leo_ads schema required")
def test_customer360_sync_is_idempotent_tenant_scoped_and_preserves_identity(test_engine, monkeypatch):
    """Verify unique source links and active-record protection against PostgreSQL."""
    source_tenant = uuid4()
    source_id = uuid4()
    tenant_key = "sync-" + uuid4().hex
    with Session(test_engine) as session:
        tenant_id = session.execute(
            text("INSERT INTO leo_ads.tenant (tenant_key, name) VALUES (:key, 'Test') RETURNING tenant_id"),
            {"key": tenant_key},
        ).scalar_one()
        session.execute(
            text("INSERT INTO leo_ads.customer360_tenant_map (customer360_tenant_id, tenant_id) VALUES (:source, :tenant)"),
            {"source": source_tenant, "tenant": tenant_id},
        )
        session.commit()
    test_engine.begin_nested()
    monkeypatch.setattr(promotions_settings, "management_tokens", SecretStr(json.dumps({tenant_key: TOKEN_A})))
    application = PromotionsApplication()
    application.engine = test_engine
    client = TestClient(application.create_app())
    headers = {"Authorization": "Bearer " + TOKEN_A}
    campaign_path = f"/management/customer360/{source_tenant}/campaigns/{source_id}"
    creative_path = f"/management/customer360/{source_tenant}/content/{source_id}"
    campaign_payload = {"name": "First", "metadata": {"source": "core"}}
    creative_payload = {"ad_type": "display", "format_code": "native", "headline": "First"}
    assert client.put(campaign_path).status_code == 401
    assert client.put(f"/management/customer360/{uuid4()}/campaigns/{source_id}", headers=headers, json=campaign_payload).status_code == 403
    first = client.put(campaign_path, headers=headers, json=campaign_payload)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "draft"
    second = client.put(campaign_path, headers=headers, json={**campaign_payload, "name": "Updated"})
    assert second.status_code == 200 and second.json()["campaign_id"] == first.json()["campaign_id"]
    creative = client.put(creative_path, headers=headers, json=creative_payload)
    assert creative.status_code == 200, creative.text
    again = client.put(creative_path, headers=headers, json={**creative_payload, "headline": "Updated"})
    assert again.status_code == 200 and again.json()["creative_id"] == creative.json()["creative_id"]
    campaign_id = first.json()["campaign_id"]
    creative_id = creative.json()["creative_id"]
    assert client.delete(f"/management/campaigns/{campaign_id}", headers=headers).status_code == 409
    assert client.delete(f"/management/creatives/{creative_id}", headers=headers).status_code == 409
    assert client.post("/management/campaigns", headers=headers, json={"campaign_key": f"customer360:{uuid4()}", "name": "Forged"}).status_code == 409
    creative_replacement = {
        "creative_key": creative.json()["creative_key"], "version_no": 2,
        "ad_type": "display", "format_code": "native",
    }
    assert client.put(f"/management/creatives/{creative_id}", headers=headers, json=creative_replacement).status_code == 409
    with Session(test_engine) as session:
        session.execute(text("UPDATE leo_ads.creative SET status = 'active' WHERE creative_id = :id"), {"id": creative_id})
        session.commit()
    assert client.put(creative_path, headers=headers, json=creative_payload).status_code == 409
