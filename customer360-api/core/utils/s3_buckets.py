"""S3-compatible bucket configuration and startup provisioning."""

from __future__ import annotations

import logging
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from leo_customer360_dao.config import settings

logger = logging.getLogger(__name__)


def build_s3_client():
    """Build an S3 client using the shared AWS/MinIO-compatible settings."""
    kwargs: dict[str, Any] = {
        "region_name": settings.event_s3_region,
        "verify": settings.event_s3_verify_ssl,
        "config": Config(
            s3={
                "addressing_style": (
                    "path" if settings.event_s3_force_path_style else "auto"
                )
            }
        ),
    }
    if settings.event_s3_endpoint_url:
        kwargs["endpoint_url"] = settings.event_s3_endpoint_url
    if settings.event_s3_access_key_id:
        kwargs["aws_access_key_id"] = settings.event_s3_access_key_id
    if settings.event_s3_secret_access_key:
        kwargs["aws_secret_access_key"] = settings.event_s3_secret_access_key
    if settings.event_s3_session_token:
        kwargs["aws_session_token"] = settings.event_s3_session_token
    return boto3.client("s3", **kwargs)


def _create_bucket(client: Any, bucket: str) -> None:
    create_args: dict[str, Any] = {"Bucket": bucket}
    if settings.event_s3_region != "us-east-1":
        create_args["CreateBucketConfiguration"] = {
            "LocationConstraint": settings.event_s3_region
        }
    try:
        client.create_bucket(**create_args)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
            raise RuntimeError(f"Could not create S3 bucket '{bucket}': {code or exc}") from exc
        try:
            client.head_bucket(Bucket=bucket)
        except ClientError as verify_exc:
            raise RuntimeError(
                f"S3 reports bucket '{bucket}' exists but it cannot be accessed"
            ) from verify_exc
    logger.info("S3 bucket is ready: %s", bucket)


def ensure_bucket_exists(client: Any, bucket: str) -> None:
    """Check a configured bucket and create it only when the endpoint reports it missing."""
    try:
        client.head_bucket(Bucket=bucket)
        logger.info("S3 bucket already exists: %s", bucket)
        return
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code not in {"404", "NoSuchBucket", "NotFound"} and status_code != 404:
            raise RuntimeError(
                f"Could not verify S3 bucket '{bucket}': {code or status_code or exc}"
            ) from exc
    if not settings.s3_auto_create_buckets:
        raise RuntimeError(
            f"S3 bucket '{bucket}' does not exist and S3_AUTO_CREATE_BUCKETS is disabled"
        )
    _create_bucket(client, bucket)


def ensure_import_buckets(client: Any | None = None) -> tuple[str, str]:
    """Ensure both configured TSV import buckets exist before API startup completes."""
    buckets = (
        settings.product_import_s3_bucket.strip(),
        settings.content_import_s3_bucket.strip(),
    )
    if any(not bucket for bucket in buckets):
        raise RuntimeError("PRODUCT_IMPORT_S3_BUCKET and CONTENT_IMPORT_S3_BUCKET must be set")
    if buckets[0] == buckets[1]:
        raise RuntimeError("Product and content import buckets must be different")

    s3_client = client or build_s3_client()
    owns_client = client is None
    try:
        for bucket in buckets:
            ensure_bucket_exists(s3_client, bucket)
    finally:
        if owns_client:
            s3_client.close()
    return buckets
