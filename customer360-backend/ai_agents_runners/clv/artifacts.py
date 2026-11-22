"""Tenant-prefixed storage for trained model artefacts and retention exports."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlparse


def model_key(tenant_id: str, domain_code: str, agent_code: str, model_version: str) -> str:
    return (
        f"models/tenant_id={tenant_id}/domain={domain_code}/"
        f"{agent_code}/{model_version}/model.txt"
    )


def valuation_export_key(tenant_id: str, as_of_month: str, batch: str) -> str:
    return (
        f"exports/cdp_customer_valuation/tenant_id={tenant_id}/"
        f"as_of_month={as_of_month}/{batch}.parquet"
    )


class ArtifactStore(Protocol):
    def put(self, key: str, payload: bytes) -> str: ...

    def get(self, uri: str) -> bytes: ...


class LocalArtifactStore:
    """Filesystem store for development and tests (CLV_ARTIFACT_DIR)."""

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)

    def put(self, key: str, payload: bytes) -> str:
        path = self.base_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path.resolve().as_uri()

    def get(self, uri: str) -> bytes:
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            raise ValueError(f"LocalArtifactStore cannot read {uri!r}")
        # as_uri() percent-encodes "=" in the tenant_id=/domain= prefixes.
        path = Path(unquote(parsed.path)).resolve()
        if self.base_dir.resolve() not in path.parents:
            raise ValueError("artefact path is outside the artefact directory")
        return path.read_bytes()


class S3ArtifactStore:
    """S3 or MinIO store using the platform's S3_* settings."""

    def __init__(self, bucket: str, client=None):
        self.bucket = bucket
        if client is None:
            import boto3
            from botocore.config import Config

            kwargs = {
                "region_name": os.environ.get("S3_REGION", "us-east-1"),
                "verify": os.environ.get("S3_VERIFY_SSL", "true").lower() != "false",
                "config": Config(
                    s3={
                        "addressing_style": "path"
                        if os.environ.get("S3_FORCE_PATH_STYLE", "false").lower() == "true"
                        else "auto"
                    }
                ),
            }
            for env_name, argument in (
                ("S3_ENDPOINT_URL", "endpoint_url"),
                ("S3_ACCESS_KEY_ID", "aws_access_key_id"),
                ("S3_SECRET_ACCESS_KEY", "aws_secret_access_key"),
            ):
                if os.environ.get(env_name):
                    kwargs[argument] = os.environ[env_name]
            client = boto3.client("s3", **kwargs)
        self.client = client

    def put(self, key: str, payload: bytes) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=payload)
        return f"s3://{self.bucket}/{key}"

    def get(self, uri: str) -> bytes:
        parsed = urlparse(uri)
        if parsed.scheme != "s3" or parsed.netloc != self.bucket:
            raise ValueError(f"S3ArtifactStore cannot read {uri!r}")
        response = self.client.get_object(Bucket=self.bucket, Key=parsed.path.lstrip("/"))
        return response["Body"].read()


def default_artifact_store() -> ArtifactStore:
    local_dir = os.environ.get("CLV_ARTIFACT_DIR")
    if local_dir:
        return LocalArtifactStore(local_dir)
    bucket = os.environ.get("CLV_MODEL_BUCKET")
    if not bucket:
        raise RuntimeError("Set CLV_MODEL_BUCKET (or CLV_ARTIFACT_DIR for local development)")
    return S3ArtifactStore(bucket)
