import json
from pathlib import Path
from decimal import Decimal
from uuid import UUID

import pytest

from core.utils.product_utils import (
    MAX_PRODUCT_TSV_BYTES,
    ProductTsvError,
    parse_product_tsv,
    stage_product_import,
)
from leo_customer360_dao.config import settings

SAMPLE_TSV = (
    Path(__file__).resolve().parents[2]
    / "customer360-seeding"
    / "data-recommendation"
    / "product-sample-data-retail.tsv"
)
FINANCE_TSV = SAMPLE_TSV.with_name("product-sample-data-finance.tsv")
TENANT_ID = UUID("11111111-1111-1111-1111-111111111111")


def test_parse_sample_product_tsv_maps_and_preserves_all_source_columns():
    records = parse_product_tsv(SAMPLE_TSV.read_bytes())

    assert len(records) == 25
    assert all(record.ext_attributes == {"product_category": "book"} for record in records)
    product = records[0]
    assert product.domain == "retail"
    assert product.product_type == "retail_product"
    assert product.source_id == "amazon"
    assert product.source_type == ""
    assert product.product_id_type == "ISBN-13"
    assert product.product_id == "978-0749484224"
    assert product.keywords == ["Digital Marketing", "Strategy"]
    assert product.original_price == Decimal("39.95")
    assert product.sale_price == Decimal("31.23")
    assert product.currency == "USD"
    assert product.ext_attributes == {"product_category": "book"}
    assert product.source_fields["Name"].startswith("Digital Marketing Strategy")
    assert product.source_fields["Description"] == ""
    assert product.image_url.startswith("https://")
    assert product.product_url.startswith("https://")


def test_parse_finance_sample_maps_stock_metadata():
    records = parse_product_tsv(FINANCE_TSV.read_bytes())

    assert len(records) == 10
    stock = records[0]
    assert stock.domain == "banking"
    assert stock.product_type == "STOCK"
    assert stock.source_id == ""
    assert stock.source_type == ""
    assert stock.product_id_type == "TICKER"
    assert stock.product_id == "NVDA"
    assert stock.ext_attributes == {
        "asset_class": "equity",
        "security_type": "common_stock",
    }


def test_parse_legacy_tsv_defaults_ext_attributes_to_empty_object():
    payload = (
        "Product_Type\tStore_ID\tProduct_ID_Type\tProduct_ID\tName\tFull_URL\n"
        "retail_product\tstore\tSKU\tsku-1\tLegacy item\thttps://example.com/item\n"
    ).encode()

    record = parse_product_tsv(payload)[0]

    assert record.ext_attributes == {}


def test_parse_tsv_maps_source_type_and_store_id_to_source_fields():
    payload = (
        "Product_Type\tStore_ID\tSource_Type\tProduct_ID_Type\tProduct_ID\tName\tFull_URL\n"
        "retail_product\tmerchant-1\tmarketplace\tSKU\tsku-1\tProduct"
        "\thttps://example.com/item\n"
    ).encode()

    record = parse_product_tsv(payload)[0]

    assert record.source_id == "merchant-1"
    assert record.source_type == "marketplace"


@pytest.mark.parametrize(
    "payload, expected_error",
    [
        (b"", "empty"),
        (b"\xff", "UTF-8"),
        (b"Product_Type\tName\nretail_product\tTest\n", "Missing required"),
        (
            b"Product_Type\tProduct_Type\tStore_ID\tProduct_ID_Type\tProduct_ID\tName\tFull_URL\n",
            "duplicate column",
        ),
        (
            (
                "Product_Type\tStore_ID\tProduct_ID_Type\tProduct_ID\tName\tFull_URL\n"
                "retail_product\tstore\tisbn\t1\tProduct\tjavascript:alert(1)\n"
            ).encode(),
            "http or https",
        ),
        (
            (
                "Product_Type\tStore_ID\tProduct_ID_Type\tProduct_ID\tName\tFull_URL\t"
                "Sale_Price\tCurrency\n"
                "retail_product\tstore\tisbn\t1\tProduct\thttps://example.com\t"
                "NaN\tUSD\n"
            ).encode(),
            "finite and non-negative",
        ),
        (
            (
                "Product_Type\tStore_ID\tProduct_ID_Type\tProduct_ID\tName\tFull_URL\t"
                "ext_attributes\n"
                'retail_product\tstore\tisbn\t1\tProduct\thttps://example.com\t[]\n'
            ).encode(),
            "JSON object",
        ),
        (
            (
                "Product_Type\tStore_ID\tProduct_ID_Type\tProduct_ID\tName\tFull_URL\t"
                "ext_attributes\n"
                'retail_product\tstore\tisbn\t1\tProduct\thttps://example.com\t{"score":NaN}\n'
            ).encode(),
            "valid JSON",
        ),
    ],
)
def test_parse_product_tsv_rejects_invalid_input(payload, expected_error):
    with pytest.raises(ProductTsvError, match=expected_error):
        parse_product_tsv(payload)


def test_parse_product_tsv_rejects_duplicate_source_product_identity():
    payload = (
        "Product_Type\tStore_ID\tProduct_ID_Type\tProduct_ID\tName\tFull_URL\n"
        "retail_product\tstore\tisbn\t1\tProduct A\thttps://example.com/a\n"
        "retail_product\tstore\tisbn\t1\tProduct B\thttps://example.com/b\n"
    ).encode()

    with pytest.raises(ProductTsvError, match="duplicate Source_Type"):
        parse_product_tsv(payload)


def test_parse_product_tsv_enforces_upload_size_limit():
    with pytest.raises(ProductTsvError, match="exceeds"):
        parse_product_tsv(b"x" * (MAX_PRODUCT_TSV_BYTES + 1))


class FakeS3Client:
    def __init__(self):
        self.put = None

    def put_object(self, **kwargs):
        self.put = kwargs


def test_stage_product_import_stages_json_with_tenant_scoped_key(monkeypatch):
    monkeypatch.setattr(settings, "product_import_s3_bucket", "product-imports")
    s3 = FakeS3Client()
    records = parse_product_tsv(SAMPLE_TSV.read_bytes())

    staged = stage_product_import(TENANT_ID, records, s3_client=s3)

    assert staged.bucket == "product-imports"
    assert staged.tenant_id == str(TENANT_ID)
    assert staged.row_count == 25
    assert staged.object_key.startswith(f"product-imports/{TENANT_ID}/")
    payload = json.loads(s3.put["Body"])
    assert payload["tenant_id"] == str(TENANT_ID)
    assert len(payload["records"]) == 25
    assert payload["records"][0]["source_fields"]["Store_ID"] == "amazon"
    assert payload["records"][0]["ext_attributes"] == {"product_category": "book"}
    assert "ServerSideEncryption" not in s3.put
