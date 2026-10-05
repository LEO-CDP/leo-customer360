import io
import json
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest

import product_import
from leo_customer360_dao.config import settings
from leo_customer360_dao.schemas.product_import import ProductImportRecord

TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
CONTENT_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")


def make_record(**overrides):
    values = {
        "domain": "retail",
        "product_type": "retail_product",
        "source_id": "store",
        "source_type": "",
        "product_id_type": "ISBN-13",
        "product_id": "978-1",
        "keywords": ["Marketing", "AI"],
        "ext_attributes": {"format": "ebook", "language": "en"},
        "original_price": Decimal("19.95"),
        "sale_price": Decimal("15.95"),
        "currency": "USD",
        "image_url": "https://example.com/image.jpg",
        "product_url": "https://example.com/product",
        "source_fields": {
            "Name": "Product title",
            "Description": "A product description",
            "Store_ID": "store",
            "Product_ID": "978-1",
            "Original_Price": "19.95",
        },
    }
    values.update(overrides)
    return ProductImportRecord.model_validate(values)


def test_generate_content_batch_sends_all_source_fields_and_validates_response(monkeypatch):
    record = make_record()
    response_body = json.dumps(
        {
            "products": [
                {
                    "product_key": record.product_key,
                    "title": "Generated title",
                    "summary": "Generated product summary",
                }
            ]
        }
    ).encode()

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return response_body

    def fake_open(request, timeout):
        assert request.full_url == settings.agent_service_url + "/products/generate-content"
        assert request.get_header("Authorization") == "Bearer test-agent-token"
        assert timeout == product_import.LLM_REQUEST_TIMEOUT_SECONDS
        request_data = json.loads(request.data)
        assert request_data["products"][0]["source_fields"] == record.source_fields
        return FakeResponse()

    monkeypatch.setattr(settings, "agent_api_token", "test-agent-token")
    result = product_import._generate_content_batch([record], opener=fake_open)

    assert result[record.product_key].title == "Generated title"


def test_generate_content_batch_rejects_missing_and_duplicate_llm_output():
    record = make_record()
    with pytest.raises(product_import.ProductImportError, match="invalid data"):
        product_import._generate_content_batch(
            [record],
            opener=lambda *_args, **_kwargs: _response(
                {"products": []}
            ),
        )

    duplicate_response = {
        "products": [
            {
                "product_key": record.product_key,
                "title": "Title one",
                "summary": "Summary one",
            },
            {
                "product_key": record.product_key,
                "title": "Title two",
                "summary": "Summary two",
            },
        ]
    }
    with pytest.raises(product_import.ProductImportError, match="invalid data|exactly once"):
        product_import._generate_content_batch(
            [record],
            opener=lambda *_args, **_kwargs: _response(duplicate_response),
        )


@pytest.mark.parametrize("configured", [True, False])
def test_agent_readiness_reports_llm_configuration(configured):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps({"llm_configured": configured}).encode()

    assert (
        product_import._agent_llm_is_configured(
            opener=lambda *_args, **_kwargs: Response()
        )
        is configured
    )


def _response(payload):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps(payload).encode()

    return Response()


def test_generate_content_batches_are_bounded():
    records = [
        make_record(product_id=f"item-{index}") for index in range(21)
    ]
    batch_sizes = []

    def generate_batch(batch):
        batch_sizes.append(len(batch))
        return {
            record.product_key: product_import.ProductContentResult(
                product_key=record.product_key,
                title=f"Title {record.product_id}",
                summary=f"Summary {record.product_id}",
            )
            for record in batch
        }

    generated = product_import.generate_content_for_records(
        records, generate_batch=generate_batch
    )

    assert batch_sizes == [20, 1]
    assert len(generated) == 21


def test_load_records_checks_tenant_prefix_and_payload_tenant(monkeypatch):
    monkeypatch.setattr(settings, "product_import_s3_bucket", "imports")
    record = make_record()
    payload = {"tenant_id": str(TENANT_ID), "records": [record.model_dump(mode="json")]}

    class FakeS3:
        def get_object(self, **_kwargs):
            return {"Body": io.BytesIO(json.dumps(payload).encode())}

    records = product_import._load_records(
        bucket="imports",
        object_key=f"product-imports/{TENANT_ID}/file.json",
        tenant_id=TENANT_ID,
        s3_client=FakeS3(),
    )

    assert records == [record]
    with pytest.raises(product_import.ProductImportError, match="outside the requested tenant"):
        product_import._load_records(
            bucket="imports",
            object_key="product-imports/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/file.json",
            tenant_id=TENANT_ID,
            s3_client=FakeS3(),
        )


def test_import_product_file_enriches_persists_and_removes_staged_object(monkeypatch):
    monkeypatch.setattr(settings, "product_import_s3_bucket", "imports")
    record = make_record()
    staged_payload = {
        "tenant_id": str(TENANT_ID),
        "records": [record.model_dump(mode="json")],
    }

    class FakeS3:
        deleted = None

        def get_object(self, **_kwargs):
            return {"Body": io.BytesIO(json.dumps(staged_payload).encode())}

        def delete_object(self, **kwargs):
            self.deleted = kwargs

    s3 = FakeS3()
    generated = product_import.ProductContentResult(
        product_key=record.product_key,
        title="Generated title",
        summary="Generated summary",
    )
    with patch.object(
        product_import,
        "persist_product_records",
        return_value=1,
    ) as persist:
        result = product_import.import_product_file(
            tenant_id=str(TENANT_ID),
            bucket="imports",
            object_key=f"product-imports/{TENANT_ID}/file.json",
            s3_client=s3,
            generate_batch=lambda batch: {batch[0].product_key: generated},
            llm_configured=lambda: True,
        )

    assert result == {
        "tenant_id": str(TENANT_ID),
        "products_imported": 1,
        "content_generation_skipped": False,
    }
    persist.assert_called_once()
    assert persist.call_args.args[0] == TENANT_ID
    assert s3.deleted == {
        "Bucket": "imports",
        "Key": f"product-imports/{TENANT_ID}/file.json",
    }


def test_import_product_file_skips_llm_and_persists_deferred_content_action(monkeypatch):
    monkeypatch.setattr(settings, "product_import_s3_bucket", "imports")
    record = make_record()
    staged_payload = {
        "tenant_id": str(TENANT_ID),
        "records": [record.model_dump(mode="json")],
    }

    class FakeS3:
        deleted = None

        def get_object(self, **_kwargs):
            return {"Body": io.BytesIO(json.dumps(staged_payload).encode())}

        def delete_object(self, **kwargs):
            self.deleted = kwargs

    s3 = FakeS3()
    with patch.object(product_import, "persist_product_records", return_value=1) as persist:
        result = product_import.import_product_file(
            tenant_id=str(TENANT_ID),
            bucket="imports",
            object_key=f"product-imports/{TENANT_ID}/deferred.json",
            s3_client=s3,
            generate_batch=lambda _batch: pytest.fail("LLM must be skipped"),
            llm_configured=lambda: False,
        )

    assert result == {
        "tenant_id": str(TENANT_ID),
        "products_imported": 1,
        "content_generation_skipped": True,
    }
    persist.assert_called_once()
    assert persist.call_args.args[2] is None
    assert s3.deleted == {
        "Bucket": "imports",
        "Key": f"product-imports/{TENANT_ID}/deferred.json",
    }


class FakeCursor:
    def __init__(self):
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query, params):
        self.executed.append((query, params))

    def fetchall(self):
        return [("retail",)]


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return self.cursor_instance


def test_persist_upserts_source_and_generated_content_in_one_tenant_transaction(monkeypatch):
    record = make_record()
    generated = {
        record.product_key: product_import.ProductContentResult(
            product_key=record.product_key,
            title="Generated product title",
            summary="Generated product description with the source price.",
        )
    }
    connection = FakeConnection()
    calls = []

    def fake_execute_values(cursor, query, rows, **kwargs):
        calls.append((query, list(rows), kwargs))
        if query == product_import.PRODUCT_UPSERT_SQL:
            return [
                (
                    record.source_type,
                    record.source_id,
                    record.product_id_type,
                    record.product_id,
                    CONTENT_ID,
                )
            ]
        return None

    monkeypatch.setattr(product_import, "execute_values", fake_execute_values)
    imported = product_import.persist_product_records(
        TENANT_ID,
        [record],
        generated,
        connection_factory=lambda: connection,
    )

    assert imported == 1
    assert connection.cursor_instance.executed[0] == (
        "SELECT set_config('app.tenant_id', %s, true)",
        (str(TENANT_ID),),
    )
    assert calls[0][0] == product_import.PRODUCT_UPSERT_SQL
    assert (
        "ON CONFLICT (tenant_id, source_type, source_id, product_id_type, product_id)"
        in calls[0][0]
    )
    assert "ext_attributes" in calls[0][0]
    product_row = calls[0][1][0]
    assert product_row[0] == uuid.UUID(hex=product_row[0]).hex
    assert product_row[1] == str(TENANT_ID)
    assert product_row[2] == uuid.UUID(hex=product_row[2]).hex
    assert product_row[8] == record.product_id == "978-1"
    assert calls[0][1][0][5:7] == (record.source_id, record.source_type)
    assert product_row[10].adapted == record.ext_attributes
    assert calls[1][0] == product_import.CONTENT_UPSERT_SQL
    assert "ON CONFLICT (content_item_id)" in calls[1][0]
    content_values = calls[1][1][0]
    assert content_values[0] == str(CONTENT_ID)
    assert content_values[1] == str(TENANT_ID)
    assert content_values[3:6] == (
        "product",
        "Generated product title",
        "Generated product description with the source price.",
    )
    assert content_values[9] == record.keywords


def test_persist_without_llm_saves_products_and_marks_content_action(monkeypatch):
    record = make_record()
    connection = FakeConnection()
    calls = []

    def fake_execute_values(cursor, query, rows, **kwargs):
        calls.append((query, list(rows), kwargs))
        return [
            (
                record.source_type,
                record.source_id,
                record.product_id_type,
                record.product_id,
                None,
            )
        ]

    monkeypatch.setattr(product_import, "execute_values", fake_execute_values)

    imported = product_import.persist_product_records(
        TENANT_ID,
        [record],
        None,
        connection_factory=lambda: connection,
    )

    assert imported == 1
    assert len(calls) == 1
    stored_row = calls[0][1][0]
    assert stored_row[2] is None
    assert stored_row[10].adapted == {
        **record.ext_attributes,
        "next_actions": "generate_content_item",
    }


def test_persist_rejects_missing_llm_output_before_database_access():
    record = make_record()
    with pytest.raises(product_import.ProductImportError, match="does not match"):
        product_import.persist_product_records(
            TENANT_ID,
            [record],
            {},
            connection_factory=lambda: pytest.fail("database should not be opened"),
        )
