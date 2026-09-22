"""Application configuration, loaded from environment variables / .env."""

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker


class PromotionsSettings(BaseSettings):
    """Application settings for the Customer 360 Promotions API."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="c360_PROMOTION_",
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


promotions_settings = PromotionsSettings()

db_promotions_engine = create_engine(
    promotions_settings.database_url,
    pool_size=promotions_settings.db_pool_size,
    max_overflow=promotions_settings.db_max_overflow,
    pool_recycle=promotions_settings.db_pool_recycle_seconds,
    pool_pre_ping=promotions_settings.db_pool_pre_ping,
    echo=promotions_settings.db_echo_sql,
    future=True,
)
