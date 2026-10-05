from botocore.exceptions import ClientError
import pytest

from core.utils.s3_buckets import (
    _create_bucket,
    ensure_bucket_exists,
    ensure_import_buckets,
)
from leo_customer360_dao.config import settings


def _client_error(code, status):
    return ClientError(
        {
            "Error": {"Code": code, "Message": code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "HeadBucket",
    )


class FakeS3Client:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.created = []
        self.headed = []
        self.closed = False

    def head_bucket(self, *, Bucket):
        self.headed.append(Bucket)
        if Bucket not in self.existing:
            raise _client_error("NoSuchBucket", 404)

    def create_bucket(self, **kwargs):
        self.created.append(kwargs)
        self.existing.add(kwargs["Bucket"])

    def close(self):
        self.closed = True


def test_ensure_import_buckets_creates_only_missing_configured_buckets(monkeypatch):
    monkeypatch.setattr(settings, "product_import_s3_bucket", "c360-product-imports")
    monkeypatch.setattr(settings, "content_import_s3_bucket", "c360-content-imports")
    monkeypatch.setattr(settings, "s3_auto_create_buckets", True)
    client = FakeS3Client(existing={"c360-content-imports"})

    buckets = ensure_import_buckets(client)

    assert buckets == ("c360-product-imports", "c360-content-imports")
    assert client.created == [{"Bucket": "c360-product-imports"}]
    assert client.headed == ["c360-product-imports", "c360-content-imports"]


def test_ensure_bucket_rejects_permission_errors_without_attempting_create(monkeypatch):
    monkeypatch.setattr(settings, "s3_auto_create_buckets", True)

    class ForbiddenClient:
        def head_bucket(self, **_kwargs):
            raise _client_error("403", 403)

        def create_bucket(self, **_kwargs):
            pytest.fail("must not create a bucket when S3 access is forbidden")

    with pytest.raises(RuntimeError, match="Could not verify S3 bucket"):
        ensure_bucket_exists(ForbiddenClient(), "private-bucket")


def test_missing_bucket_fails_when_auto_create_is_disabled(monkeypatch):
    monkeypatch.setattr(settings, "s3_auto_create_buckets", False)
    client = FakeS3Client()

    with pytest.raises(RuntimeError, match="S3_AUTO_CREATE_BUCKETS is disabled"):
        ensure_bucket_exists(client, "missing-bucket")

    assert client.created == []


def test_startup_bucket_names_must_be_distinct(monkeypatch):
    monkeypatch.setattr(settings, "product_import_s3_bucket", "same-bucket")
    monkeypatch.setattr(settings, "content_import_s3_bucket", "same-bucket")

    with pytest.raises(RuntimeError, match="must be different"):
        ensure_import_buckets(FakeS3Client())


def test_create_bucket_sends_location_constraint_only_outside_us_east_1(monkeypatch):
    monkeypatch.setattr(settings, "event_s3_region", "us-east-1")
    us_client = FakeS3Client()
    _create_bucket(us_client, "bucket-east")
    assert us_client.created == [{"Bucket": "bucket-east"}]

    monkeypatch.setattr(settings, "event_s3_region", "hcm04")
    regional_client = FakeS3Client()
    _create_bucket(regional_client, "bucket-hcm")
    assert regional_client.created == [
        {
            "Bucket": "bucket-hcm",
            "CreateBucketConfiguration": {"LocationConstraint": "hcm04"},
        }
    ]
