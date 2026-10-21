"""Approved campaign workflow activation and per-profile execution."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

SERVICE_NAME = "campaign_activation"
BUILD_DATE_TIME = os.getenv("BUILD_DATE_TIME", "unknown")
if BUILD_DATE_TIME == "unknown":
    BUILD_DATE_TIME = datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
GIT_COMMIT_HASH = os.getenv("GIT_COMMIT_HASH", "unknown")

logger = logging.getLogger(__name__)


def version_info() -> dict[str, str]:
    """Return the non-secret deployment fingerprint used in operational logs."""
    return {
        "service": SERVICE_NAME,
        "build_date_time": BUILD_DATE_TIME,
        "git_commit": GIT_COMMIT_HASH,
    }


def version_log_message() -> str:
    """Return a stable, searchable version log line."""
    info = version_info()
    return (
        "campaign_activation build "
        f"service={info['service']} build_date_time={info['build_date_time']} "
        f"git_commit={info['git_commit']}"
    )
