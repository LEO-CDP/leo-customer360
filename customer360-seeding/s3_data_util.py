import os
from pathlib import Path

from dotenv import load_dotenv
from minio import Minio
from minio.error import S3Error

load_dotenv(".env")


class S3DataUtil:
    """
    Thin wrapper around the MinIO S3 client for uploading simulator output
    files to an S3-compatible bucket. Connection settings default to the
    same MINIO_* env vars used by the project's dev MinIO stack (see
    dev-docker-compose.yml / .env.example), so it works out of the box
    against `./manage-c360.sh` / `./dev-c360.sh` locally.
    """

    def __init__(
        self,
        endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        secure: bool | None = None,
    ):
        host = os.getenv("MINIO_HOST_BIND", "localhost")
        if host in ("0.0.0.0", "127.0.0.1"):
            host = "localhost"
        port = os.getenv("MINIO_API_HOST_PORT", "9000")

        self.endpoint = endpoint or os.getenv("MINIO_ENDPOINT", f"{host}:{port}")
        self.access_key = access_key or os.getenv("MINIO_ROOT_USER")
        self.secret_key = secret_key or os.getenv("MINIO_ROOT_PASSWORD")
        self.secure = (
            secure if secure is not None
            else os.getenv("MINIO_SECURE", "false").lower() == "true"
        )

        if not self.access_key or not self.secret_key:
            raise ValueError(
                "MinIO credentials missing -- set MINIO_ROOT_USER and MINIO_ROOT_PASSWORD "
                "(see .env.example)."
            )

        self.client = Minio(
            self.endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.secure,
        )

    def _ensure_bucket(self, bucket_name: str) -> None:
        if not self.client.bucket_exists(bucket_name):
            self.client.make_bucket(bucket_name)

    def upload_file(self, local_path: str, bucket_name: str, object_name: str | None = None) -> str:
        """
        Copies a local file into `bucket_name`, creating the bucket first if
        it doesn't already exist. Returns the uploaded object's name.
        """
        local_file = Path(local_path)
        if not local_file.is_file():
            raise FileNotFoundError(f"Local file not found: {local_path}")

        object_name = object_name or local_file.name

        self._ensure_bucket(bucket_name)
        self.client.fput_object(bucket_name, object_name, str(local_file))
        return object_name


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print("Usage: python s3_data_util.py <local_file_path> <bucket_name>")
        sys.exit(1)

    local_path, bucket_name = sys.argv[1], sys.argv[2]
    try:
        uploaded_name = S3DataUtil().upload_file(local_path, bucket_name)
        print(f"Uploaded '{local_path}' to bucket '{bucket_name}' as '{uploaded_name}'.")
    except (ValueError, FileNotFoundError, S3Error) as e:
        print(f"Upload failed: {e}")
        sys.exit(1)
