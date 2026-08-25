"""Tests for manual data-source analytics API triggers."""

from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.config import settings
from core.routers.analytics_api import analytics_router
from core.utils.dagster_client import DagsterJobTriggerError, dagster_client


app = FastAPI()
app.include_router(analytics_router, prefix="/api/v1")


class FakeRedis:
    def __init__(self, states=None, running_ids=None):
        self.states = states or {}
        self.running_ids = set(running_ids or [])
        self.submission_reserved = False

    def scan_iter(self, match):
        prefix = match.removesuffix("*")
        return (key for key in self.states if key.startswith(prefix))

    def hgetall(self, key):
        return self.states.get(key, {})

    def exists(self, key):
        return int(
            key == "analytics:source-analytics-submission-lock"
            and self.submission_reserved
            or key.rsplit(":", 1)[-1] in self.running_ids
        )

    def set(self, key, _value, nx=False, ex=None):
        assert nx is True
        assert ex == 7200
        if key == "analytics:source-analytics-submission-lock":
            if self.submission_reserved:
                return False
            self.submission_reserved = True
        return True

    def hset(self, key, mapping):
        self.states.setdefault(key, {}).update(mapping)

    def hget(self, key, field):
        return self.states.get(key, {}).get(field)

    def delete(self, key):
        if key == "analytics:source-analytics-submission-lock":
            self.submission_reserved = False


def fake_redis(states=None, running_ids=None):
    return FakeRedis(states=states, running_ids=running_ids)


def test_process_data_source_logs_returns_submitted_run():
    with patch.object(settings, "sso_login", False), patch(
        "core.routers.analytics_api.get_redis_client",
        return_value=fake_redis(),
    ), patch.object(
        dagster_client.analytics, "process_tracking_logs", return_value="run-1"
    ) as trigger:
        response = TestClient(app).post("/api/v1/analytics/source-analytics/process")

    assert response.status_code == 200
    assert response.json() == {
        "status": "submitted",
        "run_id": "run-1",
        "job_name": settings.dagster_analytics_job_name,
        "message": (
            "Data-source tracking-log aggregation submitted to Dagster; "
            "poll /analytics/source-analytics/status/{run_id} for completion."
        ),
    }
    trigger.assert_called_once_with()


def test_process_data_source_logs_returns_503_when_dagster_is_unavailable():
    with patch.object(settings, "sso_login", False), patch(
        "core.routers.analytics_api.get_redis_client",
        return_value=fake_redis(),
    ), patch.object(
        dagster_client.analytics,
        "process_tracking_logs",
        side_effect=DagsterJobTriggerError("Dagster unavailable"),
    ):
        response = TestClient(app).post("/api/v1/analytics/source-analytics/process")

    assert response.status_code == 503
    assert response.json()["detail"] == "Dagster unavailable"


def test_process_data_source_logs_requires_platform_admin_in_sso_mode():
    with patch.object(settings, "sso_login", True):
        response = TestClient(app).post("/api/v1/analytics/source-analytics/process")

    assert response.status_code == 401


def test_get_data_source_logs_status_returns_dagster_status():
    status = {"run_id": "run-1", "status": "success"}
    with patch.object(settings, "sso_login", False), patch.object(
        dagster_client.analytics, "get_status", return_value=status
    ) as get_status:
        response = TestClient(app).get("/api/v1/analytics/source-analytics/status/run-1")

    assert response.status_code == 200
    assert response.json() == status
    get_status.assert_called_once_with("run-1")


def test_process_data_source_logs_returns_409_when_a_source_is_running():
    running_state = {
        "analytics:data-source-state:source-1": {
            "status": "running",
            "run_id": "run-existing",
        }
    }
    with patch.object(settings, "sso_login", False), patch(
        "core.routers.analytics_api.get_redis_client",
        return_value=fake_redis(running_state, running_ids={"source-1"}),
    ), patch.object(dagster_client.analytics, "process_tracking_logs") as trigger:
            response = TestClient(app).post("/api/v1/analytics/source-analytics/process")

    assert response.status_code == 409
    assert response.json()["detail"]["running_data_source_ids"] == ["source-1"]
    trigger.assert_not_called()


def test_get_source_analytics_status_returns_source_state_and_triggerability():
    states = {
        "analytics:data-source-state:source-1": {
            "status": "completed",
            "last_processed_hour": "2026-08-25-08",
            "last_processed_object": "2026-08-25-08/one.jsonl",
        },
        "analytics:data-source-state:source-2": {"status": "running"},
    }
    with patch.object(settings, "sso_login", False), patch(
        "core.routers.analytics_api.get_redis_client",
        return_value=fake_redis(states, running_ids={"source-2"}),
    ):
        response = TestClient(app).get("/api/v1/analytics/source-analytics/status")

    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert response.json()["can_trigger"] is False
    assert response.json()["running_data_source_ids"] == ["source-2"]


def test_manual_submission_lease_blocks_second_trigger():
    redis_client = fake_redis()
    with patch.object(settings, "sso_login", False), patch(
        "core.routers.analytics_api.get_redis_client", return_value=redis_client
    ), patch.object(
        dagster_client.analytics, "process_tracking_logs", return_value="run-1"
    ):
        first = TestClient(app).post("/api/v1/analytics/source-analytics/process")
        second = TestClient(app).post("/api/v1/analytics/source-analytics/process")

    assert first.status_code == 200
    assert second.status_code == 409
