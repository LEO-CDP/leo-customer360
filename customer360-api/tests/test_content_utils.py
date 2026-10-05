import json
from pathlib import Path
from uuid import UUID

import pytest

from core.utils.content_utils import (
    MAX_CONTENT_TSV_BYTES,
    ContentTsvError,
    parse_content_tsv,
    stage_content_import,
)
from leo_customer360_dao.config import settings

SAMPLE_TSV = (
    Path(__file__).resolve().parents[2]
    / "customer360-seeding"
    / "data-recommendation"
    / "sample-content-items.tsv"
)
TENANT_ID = UUID("11111111-1111-1111-1111-111111111111")


def test_parse_sample_content_tsv_maps_all_items_and_typed_fields():
    records = parse_content_tsv(SAMPLE_TSV.read_bytes())

    assert len(records) == 5
    assert records[0].domain == "retail"
    assert records[0].item_type == "article"
    assert records[0].title == "Smart shopping guide"
    assert records[0].segment_tags == ["shopper", "retail"]
    assert records[0].published_at.isoformat() == "2026-09-15T00:00:00+00:00"
    assert records[0].status_code == 1


@pytest.mark.parametrize(
    "payload, error",
    [
        (b"", "empty"),
        (b"Domain\tTitle\nretail\tA\n", "Missing required"),
        (
            b"Domain\tItem_Type\tTitle\tUnknown\nretail\tarticle\tA\tx\n",
            "Unsupported TSV column",
        ),
        (
            b"Domain\tItem_Type\tTitle\nretail\tproduct\tProduct row\n",
            "item_type",
        ),
        (
            b"Domain\tItem_Type\tTitle\tImage_URL\nretail\tarticle\tA\tjavascript:alert(1)\n",
            "http or https",
        ),
        (
            b"Domain\tItem_Type\tTitle\tStatus_Code\nretail\tarticle\tA\t2\n",
            "status_code",
        ),
        (
            b"Domain\tItem_Type\tTitle\tPublished_At\nretail\tarticle\tA\t2026-09-15T00:00:00\n",
            "timezone",
        ),
    ],
)
def test_parse_content_tsv_rejects_invalid_fields_and_types(payload, error):
    with pytest.raises(ContentTsvError, match=error):
        parse_content_tsv(payload)


def test_parse_content_tsv_enforces_size_limit():
    with pytest.raises(ContentTsvError, match="exceeds"):
        parse_content_tsv(b"x" * (MAX_CONTENT_TSV_BYTES + 1))


class FakeS3Client:
    def __init__(self):
        self.put = None

    def put_object(self, **kwargs):
        self.put = kwargs


def test_stage_content_import_stores_tenant_and_serialized_rows(monkeypatch):
    monkeypatch.setattr(settings, "content_import_s3_bucket", "content-imports")
    records = parse_content_tsv(SAMPLE_TSV.read_bytes())
    s3 = FakeS3Client()

    staged = stage_content_import(TENANT_ID, records, s3_client=s3)

    assert staged.bucket == "content-imports"
    assert staged.tenant_id == str(TENANT_ID)
    assert staged.row_count == 5
    assert staged.object_key.startswith(f"content-imports/{TENANT_ID}/")
    payload = json.loads(s3.put["Body"])
    assert payload["tenant_id"] == str(TENANT_ID)
    assert payload["records"][0]["item_type"] == "article"
