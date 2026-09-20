"""Infrastructure configuration for the notification engine.

Tenant-scoped Zalo connector settings are loaded from PostgreSQL by
``provider_config``. This module only owns process/database and S3 settings.
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

# --- Tracking token (shared secret with data-tracking-api + email_engine) ---
TRACKING_SECRET = os.environ.get("EMAIL_TRACKING_SECRET", "leocdp-dev-tracking-secret")

# --- S3 event lake (opt-out projection reader) ---
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL") or None
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
S3_ACCESS_KEY_ID = os.environ.get("S3_ACCESS_KEY_ID") or None
S3_SECRET_ACCESS_KEY = os.environ.get("S3_SECRET_ACCESS_KEY") or None
S3_SESSION_TOKEN = os.environ.get("S3_SESSION_TOKEN") or None
S3_FORCE_PATH_STYLE = _flag("S3_FORCE_PATH_STYLE", "true")
S3_VERIFY_SSL = _flag("S3_VERIFY_SSL", "true")
# Bronze event-lake prefix -- MUST match the writer (analytics ANALYTICS_EVENT_RAW_PREFIX);
# keys are "<prefix>/YYYY-MM-DD-HH/*.jsonl[.gz]", so listing must be prefix-scoped.
EVENT_RAW_PREFIX = os.environ.get("ANALYTICS_EVENT_RAW_PREFIX", "events").strip().strip("/")
