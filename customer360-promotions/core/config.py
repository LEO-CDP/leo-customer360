"""Application configuration, loaded from environment variables / .env."""

from typing import Optional

from pydantic import Field
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

    # --- Ad personalisation (specs/003-ad-personalization-engine) -----------
    # Initial values from the plan and research; revisit against measured
    # baselines once the hold-out group exists.

    # Hard time budget for the personalisation step of /serve. Past it, the
    # request falls back to default ranking (never an error).
    personalization_time_budget_ms: int = 25

    # A `view` needs at least this much on-screen time and visible share.
    view_min_dwell_ms: int = 1000
    view_min_visible_ratio: float = 0.5

    # Similar-visitor patterns are only published for cohorts at least this big.
    cohort_min_size: int = 50

    # Recency decay and clipping for the visitor affinity factor.
    affinity_half_life_days: int = 14
    affinity_weight_clip_min: float = 0.5
    affinity_weight_clip_max: float = 2.0

    # Strength (pseudo-impressions) of the Beta prior centred on the baseline.
    beta_prior_strength: int = 20

    candidate_cache_ttl_seconds: int = 60
    settings_cache_ttl_seconds: int = 60

    # Event ingestion limits.
    event_batch_max: int = 50
    event_buffer_max: int = 50000
    event_rate_limit_per_minute: int = 120
    # Consent changes per visitor per minute (a legitimate visitor needs one or two).
    consent_rate_limit_per_minute: int = 30

    # Kill switch for enforcing targeting, frequency caps, validity windows
    # and campaign dates on ALL delivery (see research D3). On by default.
    eligibility_enforced: bool = True

    # New ads get at least this share of eligible impressions until they have
    # `new_ad_impression_threshold` impressions.
    new_ad_min_share: float = 0.05
    new_ad_impression_threshold: int = 1000

    # Visitors with fewer qualifying events than this also use cohort patterns.
    low_history_event_threshold: int = 5

    # Replay window for signed conversion postbacks.
    conversion_replay_window_seconds: int = 300

    # Per-tenant secrets that sign server-to-server conversion postbacks:
    # a JSON object mapping tenant key to secret, from
    # c360_PROMOTION_CONVERSION_SECRETS (for example {"demo": "<random>"}).
    # POST /conversions is disabled until at least one is set. Never log it:
    # repr is off so the values cannot leak through logging the settings.
    conversion_secrets: dict[str, str] = Field(default_factory=dict, repr=False)

    def conversion_secret_for(self, tenant_key: str) -> Optional[str]:
        """The conversion secret for a tenant key, or None when it has none."""
        secret = self.conversion_secrets.get(tenant_key)
        return secret or None

    # Secret that per-tenant visitor-key secrets are derived from. Never log
    # it. Left unset, personalisation fails closed to default ranking outside
    # development (see `effective_visitor_key_secret`).
    visitor_key_master_secret: Optional[str] = None

    @property
    def effective_visitor_key_secret(self) -> Optional[str]:
        """The master secret to use, or None when personalisation must stay off.

        In development a fixed, clearly non-secret value is used so the engine
        works out of the box. In every other environment a missing secret
        means None: visitor keys cannot be derived, so serving degrades to
        default ranking rather than failing or using a guessable key.
        """
        if self.visitor_key_master_secret:
            return self.visitor_key_master_secret
        if self.environment == "development":
            return "development-only-visitor-key-secret"
        return None

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
    # Database errors and SQL echo must never print bound parameters: queries on
    # visitor profiles and consent carry the hashed visitor key, and no log line
    # may contain a full visitor key (T097). Statements are still shown.
    hide_parameters=True,
)
