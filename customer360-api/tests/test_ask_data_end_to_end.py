"""POST /ai/ask-data end to end: the real agent client over real HTTP to a stub agent,
the real router and repository, and the real restricted role on the seeded
`customer360_agents_test` database. Only the model is faked (the stub agent returns
scripted SQL and text). Skipped unless ASK_DATA_TEST_DATABASE_URL is set."""

import json
import os
import socketserver
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

sa = pytest.importorskip("sqlalchemy")

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from core.database import get_db  # noqa: E402
from core.repositories.ask_data_repository import clear_docs_cache  # noqa: E402
from core.routers.ask_data_api import ask_data_router  # noqa: E402
from leo_customer360_dao.config import settings  # noqa: E402

URL = os.environ.get("ASK_DATA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="ASK_DATA_TEST_DATABASE_URL not set")

DEMO_TENANT = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_TENANT = uuid.UUID("22222222-2222-2222-2222-222222222222")
TOKEN = "test-agent-token"
COUNT_PROFILES = "SELECT COUNT(*) AS n FROM customer360.cdp_master_profiles"


class _QuickServer(ThreadingHTTPServer):
    def server_bind(self):
        # HTTPServer.server_bind does a reverse-DNS lookup (socket.getfqdn), which can stall for ~30 s
        # on some networks. A test stub does not need a host name.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


class _StubAgent:
    """A local HTTP server standing in for customer360-agent. Replies are set per test."""

    def __init__(self):
        self.replies: dict[str, dict] = {}
        self.requests: list[tuple[str, dict, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append((self.path, body, self.headers.get("Authorization", "")))
                reply = outer.replies.get(self.path, {"detail": "no reply set"})
                code = 200 if self.path in outer.replies else 502
                data = json.dumps(reply).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):  # keep test output quiet
                pass

        self.server = _QuickServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()

    def say_sql(self, sql, status="ok"):
        self.replies["/analytics/sql"] = {"status": status, "sql": sql, "error": None}
        self.replies["/analytics/narrative"] = {"narrative": "Narrated.", "ungrounded": [], "chart": None}

    def calls(self, path):
        return [body for p, body, _ in self.requests if p == path]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture(scope="module")
def engine():
    assert URL.rstrip("/").endswith("customer360_agents_test"), "refusing to run outside the test database"
    eng = sa.create_engine(URL, future=True)
    yield eng
    eng.dispose()


@pytest.fixture
def agent():
    stub = _StubAgent()
    with patch.object(settings, "agent_service_url", stub.url), patch.object(
        settings, "agent_api_token", TOKEN
    ), patch.object(settings, "ask_data_db_role", "customer360_ask_data"):
        clear_docs_cache()
        yield stub
    stub.close()


def _client(engine, tenant=DEMO_TENANT):
    app = FastAPI()
    session_factory = sessionmaker(bind=engine, future=True)

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        request.state.tenant_id = str(tenant)
        return await call_next(request)

    def _db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.include_router(ask_data_router)
    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def test_a_question_is_answered_from_the_real_database(engine, agent):
    agent.say_sql(COUNT_PROFILES)

    resp = _client(engine).post("/ai/ask-data", json={"question": "how many profiles are there"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok" and body["rows"] == [{"n": 700}] and body["narrative"] == "Narrated."
    assert agent.calls("/analytics/narrative")[0]["rows"] == [{"n": 700}]


def test_the_agent_is_called_with_the_bearer_token_and_only_readable_columns(engine, agent):
    agent.say_sql(COUNT_PROFILES)

    _client(engine).post("/ai/ask-data", json={"question": "how many profiles are there"})

    path, body, auth = agent.requests[0]
    assert path == "/analytics/sql" and auth == f"Bearer {TOKEN}"
    doc = body["schema"]["cdp_master_profiles"].replace("cdp_master_profiles", "")
    assert "lifecycle_stage" in doc
    for hidden in ("email", "phone_number", "full_name", "address"):
        assert hidden not in doc
    assert "sys_user" not in body["schema"] and "cdp_persona_features" not in body["schema"]


def test_a_request_for_a_personal_column_is_refused_by_the_database(engine, agent):
    agent.say_sql("SELECT email FROM customer360.cdp_master_profiles")

    resp = _client(engine).post("/ai/ask-data", json={"question": "list emails"})

    body = resp.json()
    assert resp.status_code == 200 and body["status"] == "error" and "permission denied" in body["error"]
    assert body["rows"] is None and agent.calls("/analytics/narrative") == []


def test_sql_that_tries_to_change_the_tenant_is_refused_before_it_reaches_the_database(engine, agent):
    agent.say_sql("SELECT set_config('app.tenant_id', '22222222-2222-2222-2222-222222222222', true)")

    resp = _client(engine).post("/ai/ask-data", json={"question": "q"})

    assert resp.json()["status"] == "refused" and resp.json()["sql"] is None
    assert agent.calls("/analytics/narrative") == []


def test_a_write_proposed_by_the_agent_is_refused_and_nothing_changes(engine, agent):
    agent.say_sql("DELETE FROM customer360.cdp_segments")
    before = sa.text("SELECT COUNT(*) FROM customer360.cdp_segments")

    resp = _client(engine).post("/ai/ask-data", json={"question": "remove all segments"})

    assert resp.json()["status"] == "refused"
    with engine.connect() as conn:
        assert conn.execute(before).scalar() == 8


def test_another_tenant_gets_an_empty_answer(engine, agent):
    agent.say_sql(COUNT_PROFILES)

    resp = _client(engine, tenant=OTHER_TENANT).post("/ai/ask-data", json={"question": "how many profiles"})

    assert resp.json()["status"] == "ok" and resp.json()["rows"] == [{"n": 0}]


def test_the_agent_saying_not_found_is_a_normal_answer(engine, agent):
    agent.replies["/analytics/sql"] = {"status": "not_found", "sql": None, "error": None}

    resp = _client(engine).post("/ai/ask-data", json={"question": "who are our biggest spenders"})

    assert resp.status_code == 200 and resp.json()["status"] == "not_found"


def test_an_unreachable_agent_is_a_502(engine, agent):
    agent.close()  # nothing is listening any more

    resp = _client(engine).post("/ai/ask-data", json={"question": "how many profiles"})

    assert resp.status_code == 502


def test_a_missing_database_role_is_a_503(engine, agent):
    agent.say_sql(COUNT_PROFILES)

    with patch.object(settings, "ask_data_db_role", "no_such_role"):
        clear_docs_cache()
        resp = _client(engine).post("/ai/ask-data", json={"question": "how many profiles"})

    assert resp.status_code == 503
