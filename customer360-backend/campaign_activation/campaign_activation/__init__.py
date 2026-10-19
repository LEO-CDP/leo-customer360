"""Approved campaign workflow activation and per-profile execution."""

from __future__ import annotations

import logging
import os

SERVICE_NAME = "campaign_activation"
BUILD_DATE_TIME = os.environ.get("BUILD_DATE_TIME", "unknown")
GIT_COMMIT = os.environ.get("GIT_COMMIT") or os.environ.get("GIT_SHA", "unknown")

logger = logging.getLogger(__name__)


def version_info() -> dict[str, str]:
    """Return the non-secret deployment fingerprint used in operational logs."""
    return {
        "service": SERVICE_NAME,
        "build_date_time": BUILD_DATE_TIME,
        "git_commit": GIT_COMMIT,
    }


def version_log_message() -> str:
    """Return a stable, searchable version log line."""
    info = version_info()
    return (
        "campaign_activation build "
        f"service={info['service']} build_date_time={info['build_date_time']} "
        f"git_commit={info['git_commit']}"
    )
