"""Application configuration, loaded from environment variables / .env."""

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker


class AdServerSettings(BaseSettings):
    """Application settings for the LEO Ad Server API."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="LEO_AD_",
        extra="ignore",
    )

    environment: str = "development"
    api_version: str = "1.0.0"

    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "postgres"
    db_password: str = "password"
    db_name: str = "customer360"
    db_schema: str = "leo_ads"

    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle_seconds: int = 1800
    db_pool_pre_ping: bool = True
    db_echo_sql: bool = False

    api_default_page_size: int = 100
    api_max_page_size: int = 1000

    # Redis response cache (see core/cache.py). Disconnected/misconfigured
    # Redis never breaks the API -- it just disables caching (fail open).
    redis_host: str = "localhost"
    redis_port: int = 6580
    redis_db: int = 0
    redis_password: Optional[str] = None
    cache_enabled: bool = True
    cache_ttl_seconds: int = 60

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


ad_server_settings = AdServerSettings()

db_ad_engine = create_engine(
    ad_server_settings.database_url,
    pool_size=ad_server_settings.db_pool_size,
    max_overflow=ad_server_settings.db_max_overflow,
    pool_recycle=ad_server_settings.db_pool_recycle_seconds,
    pool_pre_ping=ad_server_settings.db_pool_pre_ping,
    echo=ad_server_settings.db_echo_sql,
    future=True,
)
