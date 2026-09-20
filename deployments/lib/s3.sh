#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# s3.sh - shared S3 bucket bootstrap for service deployment scripts.
#
# The service image already contains boto3, so bucket creation runs inside the
# exact image that will be deployed. This keeps the host independent of an AWS
# CLI/Python installation and works for both UAT and production vStorage.
# ---------------------------------------------------------------------------

# ensure_s3_bucket <image> <env-file> <bucket> <auto-create>
ensure_s3_bucket() {
  local image="$1"
  local env_file="$2"
  local bucket="$3"
  local auto_create="${4:-true}"

  case "${auto_create,,}" in
    true|1|yes) ;;
    *)
      echo "   S3 auto-create disabled; skipping bucket bootstrap: $bucket"
      return 0
      ;;
  esac

  [[ -n "$image" ]] || { echo "ERROR: cannot bootstrap S3 bucket without an image." >&2; return 1; }
  [[ -f "$env_file" ]] || { echo "ERROR: S3 env file not found: $env_file" >&2; return 1; }
  [[ -n "$bucket" ]] || { echo "ERROR: master-profile S3 bucket name is empty." >&2; return 1; }

  echo "   ensuring S3 bucket exists: $bucket"
  sudo docker run --rm --network host --env-file "$env_file" --entrypoint python "$image" - "$bucket" <<'PY'
import os
import sys

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError


bucket = sys.argv[1]
endpoint = os.environ.get("S3_ENDPOINT_URL") or os.environ.get("ANALYTICS_S3_ENDPOINT_URL")
region = os.environ.get("S3_REGION") or "us-east-1"
access_key = os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("S3_ACCESS_KEY_ID")
secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY") or os.environ.get("S3_SECRET_ACCESS_KEY")
verify_ssl = os.environ.get("S3_VERIFY_SSL", "true").lower() not in {"0", "false", "no"}
force_path_style = os.environ.get("S3_FORCE_PATH_STYLE", "true").lower() in {"1", "true", "yes"}

if not endpoint or not access_key or not secret_key:
    raise SystemExit("S3 endpoint and credentials are required for bucket bootstrap")

client_kwargs = {
    "region_name": region,
    "verify": verify_ssl,
    "aws_access_key_id": access_key,
    "aws_secret_access_key": secret_key,
    "config": Config(s3={"addressing_style": "path" if force_path_style else "auto"}),
}
if endpoint:
    client_kwargs["endpoint_url"] = endpoint

try:
    client = boto3.client("s3", **client_kwargs)
    client.head_bucket(Bucket=bucket)
    print(f"   S3 bucket already exists: {bucket}")
except ClientError as exc:
    error = exc.response.get("Error", {})
    code = str(error.get("Code", ""))
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    if code not in {"404", "NoSuchBucket", "NotFound"} and status != 404:
        raise SystemExit(f"S3 head_bucket failed for {bucket}: {code or status}") from exc

    create_kwargs = {"Bucket": bucket}
    if region != "us-east-1":
        create_kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    try:
        client.create_bucket(**create_kwargs)
        print(f"   created S3 bucket: {bucket}")
    except ClientError as create_exc:
        create_code = str(create_exc.response.get("Error", {}).get("Code", ""))
        if create_code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
            raise SystemExit(f"S3 create_bucket failed for {bucket}: {create_code}") from create_exc
        print(f"   S3 bucket already exists: {bucket}")
except BotoCoreError as exc:
    raise SystemExit(f"S3 bucket check failed for {bucket}: {exc}") from exc
PY
}