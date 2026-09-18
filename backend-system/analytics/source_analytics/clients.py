"""External client factories used by the analytics service."""

from typing import Any

from .config import AnalyticsSettings


class AnalyticsClientFactory:
    """Construct S3, Redis, and PostgreSQL clients from shared settings."""

    def __init__(self, settings: AnalyticsSettings) -> None:
        self.settings = settings

    def build_s3_client(self) -> Any:
        import boto3
        from botocore.client import Config

        client_kwargs: dict[str, Any] = {
            "region_name": self.settings.s3_region,
            "verify": self.settings.s3_verify_ssl,
            "config": Config(
                s3={
                    "addressing_style": (
                        "path" if self.settings.s3_force_path_style else "auto"
                    )
                },
                max_pool_connections=max(8, self.settings.s3_max_pool_connections),
            ),
        }
        if self.settings.s3_endpoint_url:
            client_kwargs["endpoint_url"] = self.settings.s3_endpoint_url
        if self.settings.s3_access_key_id:
            client_kwargs["aws_access_key_id"] = self.settings.s3_access_key_id
        if self.settings.s3_secret_access_key:
            client_kwargs["aws_secret_access_key"] = self.settings.s3_secret_access_key
        if self.settings.s3_session_token:
            client_kwargs["aws_session_token"] = self.settings.s3_session_token
        return boto3.client("s3", **client_kwargs)

    def build_redis_client(self) -> Any:
        import redis

        return redis.Redis(
            host=self.settings.redis_host,
            port=self.settings.redis_port,
            db=self.settings.redis_db,
            password=self.settings.redis_password,
            decode_responses=True,
        )

    def connect_database(self) -> Any:
        import psycopg2

        return psycopg2.connect(
            host=self.settings.db_host,
            dbname=self.settings.db_name,
            user=self.settings.db_user,
            password=self.settings.db_password,
            port=self.settings.db_port,
        )