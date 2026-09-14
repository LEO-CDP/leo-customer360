"""Environment loading and typed frontend-admin settings."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


def load_environment(base_dir: Path) -> None:
    """Load the local environment file, falling back to the repository root."""
    env_path = base_dir / ".env"
    if env_path.is_file():
        load_dotenv(env_path)
        return

    root_env_path = base_dir.parent / ".env"
    if root_env_path.is_file():
        load_dotenv(root_env_path)


@dataclass(frozen=True)
class FrontendSettings:
    """Runtime settings shared by frontend routes and service components."""

    base_dir: Path
    build_version: str
    sso_login: str
    api_hostname: str
    tenant_id: str
    app_host: str
    app_port: int
    observer_log_domain: str
    observer_tracking_uri: str
    observer_tracking_endpoint: str
    observer_cdn_js: str
    root_path: str
    docs_search_url: str
    docs_search_timeout: float
    docs_max_question_length: int
    docs_internal_secret: str
    docs_site_base: str
    app_start_time: str

    @classmethod
    def from_environment(cls, base_dir: Path) -> "FrontendSettings":
        """Build settings from environment variables after environment loading."""
        observer_log_domain = os.getenv("LEO_OBSERVER_LOG_DOMAIN", "beta.leocdp.com")
        observer_tracking_uri = os.getenv(
            "LEO_OBSERVER_TRACKING_URI", "/data/api/v1/tracking/logs"
        )
        root_path = os.getenv("FRONTEND_ROOT_PATH", "/c360").rstrip("/")

        return cls(
            base_dir=base_dir,
            build_version=os.getenv("BUILD_VERSION", "dev"),
            sso_login=os.getenv("SSO_LOGIN", "false").lower(),
            api_hostname=os.getenv(
                "FRONTEND_API_HOSTNAME", "http://localhost:8008"
            ).rstrip("/"),
            tenant_id=os.getenv(
                "FRONTEND_TENANT_ID", "11111111-1111-1111-1111-111111111111"
            ),
            app_host=os.getenv("HOST", "0.0.0.0"),
            app_port=int(os.getenv("PORT", "8890")),
            observer_log_domain=observer_log_domain,
            observer_tracking_uri=observer_tracking_uri,
            observer_tracking_endpoint=os.getenv(
                "LEO_OBSERVER_TRACKING_ENDPOINT",
                f"https://{observer_log_domain}{observer_tracking_uri}",
            ),
            observer_cdn_js=os.getenv(
                "LEO_OBSERVER_CDN_JS",
                "https://gcore.jsdelivr.net/gh/LEO-CDP/leo-customer360@main/"
                "data-tracking-api/static/c360-web-sdk/observer/leo.proxy.js",
            ),
            root_path=root_path,
            docs_search_url=os.getenv(
                "DOCS_SEARCH_URL", "http://127.0.0.1:8001"
            ).rstrip("/"),
            docs_search_timeout=float(
                os.getenv("DOCS_PROXY_TIMEOUT_SECONDS")
                or os.getenv("DOCS_SEARCH_TIMEOUT", "120")
            ),
            docs_max_question_length=int(os.getenv("DOCS_MAX_QUESTION_LEN", "2000")),
            docs_internal_secret=(
                os.getenv("DOCS_INTERNAL_AUTH_SECRET")
                or os.getenv("DOCS_INTERNAL_SECRET", "leoragbot")
            ),
            docs_site_base=os.getenv(
                "DOCS_SITE_BASE", "https://leo-cdp.github.io/leo-customer360"
            ).rstrip("/"),
            app_start_time=datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
        )

    @property
    def is_dev(self) -> bool:
        return self.sso_login in {"false", "0", "no"}

    @property
    def api_base(self) -> str:
        return f"{self.api_hostname}/api/v1"

    @property
    def static_base(self) -> str:
        return f"{self.root_path}/static"

    @property
    def docs_ai_base(self) -> str:
        return f"{self.root_path}/ai" if self.root_path and self.root_path != "/" else "/ai"
