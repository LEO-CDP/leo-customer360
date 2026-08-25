"""API endpoints for manually triggering tracking-log analytics."""

import logging
from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from redis.exceptions import RedisError

from core.cache import get_redis_client
from core.config import settings
from core.utils.dagster_client import DagsterJobTriggerError, dagster_client

logger = logging.getLogger(__name__)

analytics_router = APIRouter(prefix="/analytics", tags=["Analytics"])

PLATFORM_ADMIN_ROLES = {"platform_admin", "super_admin", "system_admin"}
SOURCE_LOCK_PREFIX = "analytics:data-source-lock:"
SOURCE_STATE_PREFIX = "analytics:data-source-state:"
SUBMISSION_LOCK_KEY = "analytics:source-analytics-submission-lock"
SUBMISSION_STATE_KEY = "analytics:source-analytics-submission-state"
SUBMISSION_LOCK_TTL_SECONDS = 7200


def _extract_roles(payload: dict[str, Any]) -> set[str]:
    roles: set[str] = set()

    def add_roles(items: Iterable[Any]) -> None:
        roles.update(
            item.strip().lower()
            for item in items
            if isinstance(item, str) and item.strip()
        )

    add_roles(payload.get("roles") or [])
    realm_access = payload.get("realm_access")
    if isinstance(realm_access, dict):
        add_roles(realm_access.get("roles") or [])
    resource_access = payload.get("resource_access")
    if isinstance(resource_access, dict):
        for resource in resource_access.values():
            if isinstance(resource, dict):
                add_roles(resource.get("roles") or [])
    return roles


def _enforce_analytics_permissions(request: Request) -> None:
    """Require a platform administrator for the cross-tenant batch in SSO mode."""
    if not settings.sso_login:
        return

    payload = getattr(request.state, "user", None)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=401, detail="Authentication required")
    if payload.get("is_platform_admin") is True:
        return
    if not _extract_roles(payload) & PLATFORM_ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Platform admin role required to run analytics")


def _analytics_status() -> dict[str, Any]:
    """Read source locks and persisted state for the admin UI."""
    redis_client = get_redis_client()
    if redis_client is None:
        raise HTTPException(status_code=503, detail="Analytics status is unavailable without Redis")

    try:
        statuses: list[dict[str, str]] = []
        for state_key in redis_client.scan_iter(match=f"{SOURCE_STATE_PREFIX}*"):
            data_source_id = str(state_key)[len(SOURCE_STATE_PREFIX):]
            state = {
                str(key): str(value)
                for key, value in redis_client.hgetall(state_key).items()
            }
            if redis_client.exists(f"{SOURCE_LOCK_PREFIX}{data_source_id}"):
                state["status"] = "running"
            elif state.get("status") == "running":
                state["status"] = "stale"
            state["data_source_id"] = data_source_id
            statuses.append(state)
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="Analytics status is temporarily unavailable") from exc

    statuses.sort(key=lambda status: status["data_source_id"])
    try:
        submission_state = {
            str(key): str(value)
            for key, value in redis_client.hgetall(SUBMISSION_STATE_KEY).items()
        }
        submission_active = bool(redis_client.exists(SUBMISSION_LOCK_KEY))
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="Analytics status is temporarily unavailable") from exc

    if submission_active and submission_state.get("run_id"):
        submission_state["status"] = "submitted"
    running_ids = [
        status["data_source_id"] for status in statuses if status.get("status") == "running"
    ]
    return {
        "status": "running" if running_ids or submission_active else "idle",
        "can_trigger": not running_ids and not submission_active,
        "running_data_source_ids": running_ids,
        "data_sources": statuses,
        "active_submission": submission_state if submission_active else None,
    }


def _reserve_submission(redis_client: Any) -> bool:
    return bool(
        redis_client.set(
            SUBMISSION_LOCK_KEY,
            "reserved",
            nx=True,
            ex=SUBMISSION_LOCK_TTL_SECONDS,
        )
    )


def _release_submission(redis_client: Any) -> None:
    redis_client.delete(SUBMISSION_LOCK_KEY)


@analytics_router.get("/source-analytics/status")
def get_source_analytics_status(request: Request) -> dict[str, Any]:
    """Return per-source running state and persisted processing cursors."""
    _enforce_analytics_permissions(request)
    return _analytics_status()


@analytics_router.post("/source-analytics/process")
def process_source_analytics(request: Request) -> dict[str, str]:
    """Submit one asynchronous cross-tenant tracking-log aggregation run."""
    _enforce_analytics_permissions(request)
    status = _analytics_status()
    if not status["can_trigger"]:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "An analytics run is already processing one or more data sources",
                "running_data_source_ids": status["running_data_source_ids"],
            },
        )
    redis_client = get_redis_client()
    if redis_client is None:
        raise HTTPException(status_code=503, detail="Analytics trigger is unavailable without Redis")
    try:
        if not _reserve_submission(redis_client):
            raise HTTPException(status_code=409, detail="An analytics run is already queued or running")
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="Analytics trigger is temporarily unavailable") from exc
    try:
        run_id = dagster_client.analytics.process_tracking_logs()
    except DagsterJobTriggerError as exc:
        _release_submission(redis_client)
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        redis_client.hset(
            SUBMISSION_STATE_KEY,
            mapping={"run_id": run_id, "status": "submitted", "trigger_reason": "manual_api"},
        )
    except RedisError as exc:
        _release_submission(redis_client)
        raise HTTPException(status_code=503, detail="Analytics status could not be recorded") from exc

    return {
        "status": "submitted",
        "run_id": run_id,
        "job_name": settings.dagster_analytics_job_name,
        "message": (
            "Data-source tracking-log aggregation submitted to Dagster; "
            "poll /analytics/source-analytics/status/{run_id} for completion."
        ),
    }


@analytics_router.get("/source-analytics/status/{run_id}")
def get_source_analytics_run_status(request: Request, run_id: str) -> dict[str, Any]:
    """Return the status of a manually submitted analytics run."""
    _enforce_analytics_permissions(request)
    try:
        result = dagster_client.analytics.get_status(run_id)
        if result["status"] in {"success", "failure"}:
            redis_client = get_redis_client()
            if redis_client is not None:
                try:
                    if redis_client.hget(SUBMISSION_STATE_KEY, "run_id") == run_id:
                        _release_submission(redis_client)
                except RedisError:
                    logger.warning("Could not clear analytics submission lease", exc_info=True)
        return result
    except DagsterJobTriggerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


all_analytics_routers = [analytics_router]
