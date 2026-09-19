"""Centralized environment configuration for the notification engine.

All ``os.environ`` reads for this code location live here (single
``load_dotenv``), so the rest of the package imports typed module constants
instead of scattering env lookups. Zalo Open API endpoints default to the
current Zalo OA v4 values -- ⚠️ confirm against current docs before production.
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _flag(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes"}


# --- Database ---
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_NAME = os.environ.get("DB_NAME", "customer360")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "postgres")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_SCHEMA = os.environ.get("DB_SCHEMA", "customer360")

# --- Zalo OA credentials + endpoints ---
ZALO_APP_ID = os.environ.get("CRM_ZALO_OA_APP_ID", "")
ZALO_APP_SECRET = os.environ.get("CRM_ZALO_OA_APP_SECRET", "")
ZALO_TOKEN_URL = os.environ.get("CRM_ZALO_OA_TOKEN_URL", "https://oauth.zaloapp.com/v4/oa/access_token")
ZNS_API_BASE = os.environ.get("CRM_ZALO_ZNS_API_BASE_URL", "https://business.openapi.zalo.me").rstrip("/")

# --- Dispatch ---
DISPATCH_ADAPTER = os.environ.get("CRM_ZALO_DISPATCH_ADAPTER", "mock")
BATCH_SIZE = int(os.environ.get("CRM_ZALO_BATCH_SIZE", "500"))

# --- Tracking token (shared secret with data-tracking-api + email_engine) ---
TRACKING_SECRET = os.environ.get("EMAIL_TRACKING_SECRET", "leocdp-dev-tracking-secret")

# --- Dagster schedules ---
TOKEN_REFRESH_CRON = os.environ.get("CRM_ZALO_TOKEN_REFRESH_CRON", "*/30 * * * *")
OPTOUT_PROJECTION_CRON = os.environ.get("CRM_ZALO_OPTOUT_PROJECTION_CRON", "*/15 * * * *")

# --- S3 event lake (opt-out projection reader) ---
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL") or None
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
S3_ACCESS_KEY_ID = os.environ.get("S3_ACCESS_KEY_ID") or None
S3_SECRET_ACCESS_KEY = os.environ.get("S3_SECRET_ACCESS_KEY") or None
S3_SESSION_TOKEN = os.environ.get("S3_SESSION_TOKEN") or None
S3_FORCE_PATH_STYLE = _flag("S3_FORCE_PATH_STYLE", "true")
S3_VERIFY_SSL = _flag("S3_VERIFY_SSL", "true")
OPTOUT_LOOKBACK_HOURS = int(os.environ.get("CRM_ZALO_OPTOUT_LOOKBACK_HOURS", "6"))
# Bronze event-lake prefix -- MUST match the writer (analytics ANALYTICS_EVENT_RAW_PREFIX);
# keys are "<prefix>/YYYY-MM-DD-HH/*.jsonl[.gz]", so listing must be prefix-scoped.
EVENT_RAW_PREFIX = os.environ.get("ANALYTICS_EVENT_RAW_PREFIX", "events").strip().strip("/")
