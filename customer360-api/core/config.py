"""Application configuration, loaded from environment variables / .env."""

from typing import Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = Field(
        default="development",
        validation_alias=AliasChoices("ENVIRONMENT", "environment"),
    )
    api_version: str = Field(
        default="1.0.0",
        validation_alias=AliasChoices("API_VERSION", "api_version"),
    )

    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "postgres"
    db_password: str = "password"
    db_name: str = "customer360"
    db_schema: str = "customer360"

    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle_seconds: int = 1800
    db_pool_pre_ping: bool = True
    db_echo_sql: bool = False

    api_default_page_size: int = 100
    api_max_page_size: int = 1000

    # Dagster webserver GraphQL endpoint (backend-system/, `dagster dev` /
    # dagster-webserver deployment) -- shared by every backend-system code
    # location. Used to submit job runs asynchronously instead of running
    # long batch work inline inside an HTTP request (see
    # core/utils/dagster_client.py).
    dagster_graphql_host: str = Field(
        default="localhost",
        validation_alias=AliasChoices("DAGSTER_GRAPHQL_HOST", "dagster_graphql_host"),
    )
    dagster_graphql_port: int = Field(
        default=3000,
        validation_alias=AliasChoices("DAGSTER_GRAPHQL_PORT", "dagster_graphql_port"),
    )

    # Per-service job/location/repository names, one triplet per
    # `backend-system/<service>/dagster_defs.py` code location registered in
    # `backend-system/workspace.yaml`. `repository_name` defaults to
    # `__repository__`, Dagster's auto-generated name for a module-level
    # `Definitions(...)` object (every service here uses that pattern).
    dagster_analytics_job_name: str = Field(
        default="analytics_job",
        validation_alias=AliasChoices("DAGSTER_ANALYTICS_JOB_NAME", "dagster_analytics_job_name"),
    )
    dagster_analytics_location_name: str = Field(
        default="analytics",
        validation_alias=AliasChoices("DAGSTER_ANALYTICS_LOCATION_NAME", "dagster_analytics_location_name"),
    )
    dagster_analytics_repository_name: str = Field(
        default="__repository__",
        validation_alias=AliasChoices("DAGSTER_ANALYTICS_REPOSITORY_NAME", "dagster_analytics_repository_name"),
    )

    dagster_identity_resolution_job_name: str = Field(
        default="identity_resolution_job",
        validation_alias=AliasChoices(
            "DAGSTER_IDENTITY_RESOLUTION_JOB_NAME", "dagster_identity_resolution_job_name"
        ),
    )
    dagster_identity_resolution_location_name: str = Field(
        default="identity_resolution",
        validation_alias=AliasChoices(
            "DAGSTER_IDENTITY_RESOLUTION_LOCATION_NAME", "dagster_identity_resolution_location_name"
        ),
    )
    dagster_identity_resolution_repository_name: str = Field(
        default="__repository__",
        validation_alias=AliasChoices(
            "DAGSTER_IDENTITY_RESOLUTION_REPOSITORY_NAME", "dagster_identity_resolution_repository_name"
        ),
    )

    dagster_scoring_job_name: str = Field(
        default="scoring_job",
        validation_alias=AliasChoices("DAGSTER_SCORING_JOB_NAME", "dagster_scoring_job_name"),
    )
    dagster_scoring_location_name: str = Field(
        default="scoring",
        validation_alias=AliasChoices("DAGSTER_SCORING_LOCATION_NAME", "dagster_scoring_location_name"),
    )
    dagster_scoring_repository_name: str = Field(
        default="__repository__",
        validation_alias=AliasChoices("DAGSTER_SCORING_REPOSITORY_NAME", "dagster_scoring_repository_name"),
    )

    dagster_segmentation_job_name: str = Field(
        default="segmentation_job",
        validation_alias=AliasChoices("DAGSTER_SEGMENTATION_JOB_NAME", "dagster_segmentation_job_name"),
    )
    dagster_segmentation_location_name: str = Field(
        default="segmentation",
        validation_alias=AliasChoices("DAGSTER_SEGMENTATION_LOCATION_NAME", "dagster_segmentation_location_name"),
    )
    dagster_segmentation_repository_name: str = Field(
        default="__repository__",
        validation_alias=AliasChoices("DAGSTER_SEGMENTATION_REPOSITORY_NAME", "dagster_segmentation_repository_name"),
    )

    dagster_data_synch_job_name: str = Field(
        default="data_synch_job",
        validation_alias=AliasChoices("DAGSTER_DATA_SYNCH_JOB_NAME", "dagster_data_synch_job_name"),
    )
    dagster_data_synch_location_name: str = Field(
        default="data_synch",
        validation_alias=AliasChoices("DAGSTER_DATA_SYNCH_LOCATION_NAME", "dagster_data_synch_location_name"),
    )
    dagster_data_synch_repository_name: str = Field(
        default="__repository__",
        validation_alias=AliasChoices("DAGSTER_DATA_SYNCH_REPOSITORY_NAME", "dagster_data_synch_repository_name"),
    )

    dagster_email_engine_job_name: str = Field(
        default="email_engine_job",
        validation_alias=AliasChoices("DAGSTER_EMAIL_ENGINE_JOB_NAME", "dagster_email_engine_job_name"),
    )
    dagster_email_engine_location_name: str = Field(
        default="email_engine",
        validation_alias=AliasChoices("DAGSTER_EMAIL_ENGINE_LOCATION_NAME", "dagster_email_engine_location_name"),
    )
    dagster_email_engine_repository_name: str = Field(
        default="__repository__",
        validation_alias=AliasChoices("DAGSTER_EMAIL_ENGINE_REPOSITORY_NAME", "dagster_email_engine_repository_name"),
    )

    dagster_notification_engine_job_name: str = Field(
        default="notification_engine_job",
        validation_alias=AliasChoices(
            "DAGSTER_NOTIFICATION_ENGINE_JOB_NAME", "dagster_notification_engine_job_name"
        ),
    )
    dagster_notification_engine_location_name: str = Field(
        default="notification_engine",
        validation_alias=AliasChoices(
            "DAGSTER_NOTIFICATION_ENGINE_LOCATION_NAME", "dagster_notification_engine_location_name"
        ),
    )
    dagster_notification_engine_repository_name: str = Field(
        default="__repository__",
        validation_alias=AliasChoices(
            "DAGSTER_NOTIFICATION_ENGINE_REPOSITORY_NAME", "dagster_notification_engine_repository_name"
        ),
    )

    # Redis response cache (see core/cache.py). Disconnected/misconfigured
    # Redis never breaks the API -- it just disables caching (fail open).
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: Optional[str] = None
    cache_enabled: bool = True
    cache_ttl_seconds: int = 60

    # SSO/Keycloak settings 
    keycloak_callback_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "KEYCLOAK_CALLBACK_URL", "keycloak_callback_url", "keycloakCallbackUrl"
        ),
    )
    keycloak_client_id: str = Field(
        default="leocdp",
        validation_alias=AliasChoices(
            "KEYCLOAK_CLIENT_ID", "keycloak_client_id", "keycloakClientId"),
    )
    keycloak_client_secret: str = Field(
        default="",
        validation_alias=AliasChoices(
            "KEYCLOAK_CLIENT_SECRET", "keycloak_client_secret", "keycloakClientSecret"
        ),
    )
    keycloak_realm: str = Field(
        default="master",
        validation_alias=AliasChoices(
            "KEYCLOAK_REALM", "keycloak_realm", "keycloakRealm"),
    )
    keycloak_verify_ssl: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "KEYCLOAK_VERIFY_SSL", "keycloak_verify_ssl", "keycloakVerifySSL"),
    )
    sso_login: bool = Field(
        default=False,
        validation_alias=AliasChoices("SSO_LOGIN", "sso_login", "ssoLogin"),
    )
    sso_login_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "SSO_LOGIN_URL", "sso_login_url", "ssoLoginUrl"),
    )

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def sso_configs(self) -> dict[str, str]:
        return {
            "keycloakCallbackUrl": self.keycloak_callback_url,
            "keycloakClientId": self.keycloak_client_id,
            "keycloakClientSecret": self.keycloak_client_secret,
            "keycloakRealm": self.keycloak_realm,
            "keycloakVerifySSL": str(self.keycloak_verify_ssl).lower(),
            "ssoLogin": str(self.sso_login).lower(),
            "ssoLoginUrl": self.sso_login_url,
        }


settings = Settings()
