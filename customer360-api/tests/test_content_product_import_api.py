from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import require_tenant
from core.database import get_db
from core.routers.content_api import router
from core.utils.content_utils import StagedContentImport
from core.utils.dagster_client import DagsterJobTriggerError
from core.utils.product_utils import StagedProductImport

TENANT_ID = "11111111-1111-1111-1111-111111111111"
SAMPLE_TSV = b"""Product_Type\tStore_ID\tProduct_ID_Type\tProduct_ID\tName\tDescription\tImage_URL\tOriginal_Price\tSale_Price\tCurrency\tFull_URL
retail_product\tstore\tISBN\t1\tProduct name\tDescription\thttps://example.com/image.jpg\t10\t8\tUSD\thttps://example.com/product
"""
CONTENT_TSV = b"""Domain\tItem_Type\tTitle\tSummary\tSegment_Tags\tStatus_Code
retail\tarticle\tTest article\tArticle summary\tretail,article\t1
"""


def make_client():
    app = FastAPI()
    app.include_router(router)
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_tenant] = lambda: TENANT_ID
    return TestClient(app)


def test_product_tsv_upload_stages_and_submits_tenant_import():
    client = make_client()
    staged = StagedProductImport(
        bucket="product-imports",
        object_key=f"product-imports/{TENANT_ID}/upload.json",
        tenant_id=TENANT_ID,
        row_count=1,
    )
    with (
        patch("core.routers.content_api.validate_domain_value") as validate_domain,
        patch("core.routers.content_api.stage_product_import", return_value=staged) as stage,
        patch("core.routers.content_api.dagster_client") as dagster,
    ):
        dagster.product_content_import.import_products.return_value = "run-123"
        response = client.post(
            "/content-items/import/products",
            files={"file": ("products.tsv", SAMPLE_TSV, "text/tab-separated-values")},
        )

    assert response.status_code == 202
    assert response.json() == {
        "run_id": "run-123",
        "status": "submitted",
        "products_submitted": 1,
    }
    validate_domain.assert_called_once()
    stage.assert_called_once()
    dagster.product_content_import.import_products.assert_called_once_with(
        tenant_id=TENANT_ID,
        bucket="product-imports",
        object_key=staged.object_key,
    )


def test_product_tsv_upload_rejects_invalid_file_before_staging():
    client = make_client()
    with patch("core.routers.content_api.stage_product_import") as stage:
        response = client.post(
            "/content-items/import/products",
            files={"file": ("products.csv", SAMPLE_TSV, "text/csv")},
        )

    assert response.status_code == 415
    stage.assert_not_called()


def test_product_tsv_upload_deletes_stage_when_dagster_submission_fails():
    client = make_client()
    staged = StagedProductImport(
        bucket="product-imports",
        object_key=f"product-imports/{TENANT_ID}/upload.json",
        tenant_id=TENANT_ID,
        row_count=1,
    )
    with (
        patch("core.routers.content_api.validate_domain_value"),
        patch("core.routers.content_api.stage_product_import", return_value=staged),
        patch("core.routers.content_api.delete_staged_import") as delete_stage,
        patch("core.routers.content_api.dagster_client") as dagster,
    ):
        dagster.product_content_import.import_products.side_effect = DagsterJobTriggerError(
            "Dagster unavailable"
        )
        response = client.post(
            "/content-items/import/products",
            files={"file": ("products.tsv", SAMPLE_TSV, "text/tab-separated-values")},
        )

    assert response.status_code == 503
    assert "Dagster unavailable" in response.json()["detail"]
    delete_stage.assert_called_once_with(staged.bucket, staged.object_key)


def test_content_tsv_upload_stages_and_submits_tenant_import():
    client = make_client()
    staged = StagedContentImport(
        bucket="content-imports",
        object_key=f"content-imports/{TENANT_ID}/upload.json",
        tenant_id=TENANT_ID,
        row_count=1,
    )
    with (
        patch("core.routers.content_api.validate_domain_value") as validate_domain,
        patch("core.routers.content_api.stage_content_import", return_value=staged) as stage,
        patch("core.routers.content_api.dagster_client") as dagster,
    ):
        dagster.content_item_import.import_content.return_value = "content-run-123"
        response = client.post(
            "/content-items/import/content",
            files={"file": ("content.tsv", CONTENT_TSV, "text/tab-separated-values")},
        )

    assert response.status_code == 202
    assert response.json() == {
        "run_id": "content-run-123",
        "status": "submitted",
        "content_items_submitted": 1,
    }
    validate_domain.assert_called_once()
    stage.assert_called_once()
    dagster.content_item_import.import_content.assert_called_once_with(
        tenant_id=TENANT_ID,
        bucket="content-imports",
        object_key=staged.object_key,
    )


def test_content_tsv_upload_rejects_invalid_content_type_before_staging():
    client = make_client()
    with patch("core.routers.content_api.stage_content_import") as stage:
        response = client.post(
            "/content-items/import/content",
            files={"file": ("products.tsv", CONTENT_TSV, "text/tab-separated-values")},
        )

    assert response.status_code == 422
    stage.assert_not_called()


def test_content_item_create_rejects_a_body_tenant_different_from_auth_context():
    client = make_client()
    with patch("core.routers.content_api.ContentRepository") as repository:
        response = client.post(
            "/content-items/",
            json={
                "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "domain": "retail",
                "item_type": "article",
                "title": "An article",
            },
        )

    assert response.status_code == 403
    repository.assert_not_called()
