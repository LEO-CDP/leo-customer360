"""Configuration for the promotions service integration."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class PromotionSettings(BaseSettings):
    """Read promotion connection settings from environment or local .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    promotion_api_url: str = Field(default="", validation_alias="C360_PROMOTION_API_URL")
    promotion_management_tokens: SecretStr = Field(
        default=SecretStr("{}"), validation_alias="c360_PROMOTION_MANAGEMENT_TOKENS"
    )
    promotion_tenant_keys: str = Field(default="{}", validation_alias="C360_PROMOTION_TENANT_KEYS")
    promotion_allow_insecure_http: bool = Field(
        default=False, validation_alias="C360_PROMOTION_ALLOW_INSECURE_HTTP"
    )


promotion_settings = PromotionSettings()
