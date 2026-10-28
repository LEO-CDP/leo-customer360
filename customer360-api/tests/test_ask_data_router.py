"""POST /ai/ask-data (core.routers.ask_data_api). Real router, patched repository; a
middleware sets request.state.tenant_id the way core/auth.py does."""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from core.database import get_db
from core.repositories.ask_data_repository import (
    AskDataAgentError,
    AskDataRepository,
    AskDataResult,
    AskDataUnavailable,
)
from core.routers.ask_data_api import ask_data_router
from leo_customer360_dao.config import settings

MODULE = "core.routers.ask_data_api"
TENANT = uuid.uuid4()
URL = "/ai/ask-data"

OK = AskDataResult(
    status="ok",
    sql="SELECT COUNT(*) AS n FROM customer360.cdp_segments",
    rows=[{"n": 8}],
    truncated=False,
    narrative="There are 8 segments.",
    ungrounded=[],
    chart={"type": "metric", "y": ["n"]},
)


def _client(tenant=TENANT):
    app = FastAPI()

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        if tenant is not None:
            request.state.tenant_id = str(tenant)
        return await call_next(request)

    app.include_router(ask_data_router)
    app.dependency_overrides[get_db] = lambda: MagicMock(name="session")
    return TestClient(app)


@pytest.fixture
def repo():
    """A patched repository, with the role configured."""
    fake = MagicMock(spec=AskDataRepository)
    fake.ask.return_value = OK
    with patch(f"{MODULE}.AskDataRepository", return_value=fake) as cls, patch.object(
        settings, "ask_data_db_role", "customer360_ask_data"
    ):
        fake.cls = cls
        yield fake


def test_ok_answer_has_the_full_contract(repo):
    resp = _client().post(URL, json={"question": "how many segments are there"})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "status": "ok",
        "sql": "SELECT COUNT(*) AS n FROM customer360.cdp_segments",
        "rows": [{"n": 8}],
        "truncated": False,
        "narrative": "There are 8 segments.",
        "ungrounded": [],
        "chart": {"type": "metric", "y": ["n"]},
        "error": None,
        "narrative_error": None,
    }


def test_the_tenant_comes_from_the_authenticated_request_and_the_configured_role_is_used(repo):
    _client().post(URL, json={"question": "q"})

    assert repo.ask.call_args.args[0] == TENANT
    assert repo.cls.call_args.args[1] == "customer360_ask_data"


def test_a_tenant_in_the_body_is_rejected_not_trusted(repo):
    resp = _client().post(URL, json={"question": "q", "tenant_id": str(uuid.uuid4())})

    assert resp.status_code == 422
    repo.ask.assert_not_called()


def test_earlier_turns_are_passed_through(repo):
    history = [{"question": "how many segments", "sql": "SELECT COUNT(*) FROM customer360.cdp_segments"}]

    _client().post(URL, json={"question": "now split that", "history": history})

    assert repo.ask.call_args.args[2] == history


def test_no_tenant_context_is_a_400(repo):
    resp = _client(tenant=None).post(URL, json={"question": "q"})

    assert resp.status_code == 400
    repo.ask.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [
        {"question": ""},
        {"question": "x" * 2001},
        {},
        {"question": "q", "unexpected": 1},
        {"question": "q", "history": [{"question": "a", "sql": "b"}] * 6},
        {"question": "q", "history": [{"question": "a"}]},
    ],
    ids=["empty", "too_long", "missing", "extra_field", "too_much_history", "bad_turn"],
)
def test_invalid_requests_are_a_422(repo, body):
    assert _client().post(URL, json=body).status_code == 422
    repo.ask.assert_not_called()


@pytest.mark.parametrize(
    "result",
    [
        AskDataResult(status="not_found"),
        AskDataResult(status="refused", error="Refused, only a single read-only SELECT is allowed"),
        AskDataResult(status="error", sql="SELECT email FROM x", error="permission denied for table x"),
    ],
    ids=["not_found", "refused", "error"],
)
def test_flagged_answers_are_normal_200_responses(repo, result):
    repo.ask.return_value = result

    resp = _client().post(URL, json={"question": "q"})

    assert resp.status_code == 200
    assert resp.json()["status"] == result.status


def test_an_unconfigured_role_closes_the_route_without_touching_the_repository():
    with patch(f"{MODULE}.AskDataRepository") as cls, patch.object(settings, "ask_data_db_role", ""):
        resp = _client().post(URL, json={"question": "q"})

    assert resp.status_code == 503
    cls.assert_not_called()


def test_an_unusable_role_is_a_503_that_does_not_leak_database_detail(repo):
    repo.ask.side_effect = AskDataUnavailable("role 'customer360_ask_data' is not usable: permission denied to set role")

    resp = _client().post(URL, json={"question": "q"})

    assert resp.status_code == 503
    assert "permission denied" not in resp.text and "customer360_ask_data" not in resp.text


def test_an_agent_failure_is_a_502_with_its_message(repo):
    repo.ask.side_effect = AskDataAgentError("AI agent error: upstream timeout")

    resp = _client().post(URL, json={"question": "q"})

    assert resp.status_code == 502
    assert "upstream timeout" in resp.json()["detail"]


def test_the_default_role_is_the_one_the_migration_creates():
    from leo_customer360_dao.config import Settings

    assert Settings().ask_data_db_role == "customer360_ask_data"
