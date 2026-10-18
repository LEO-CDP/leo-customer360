"""A prompt that is not published in the prompt store is an operator problem,
not a crash: the service answers 502 and names the missing key, for the
existing planners and the use-case agents alike."""

from fastapi.testclient import TestClient

import app as app_module
import use_cases.instructions as ins
from prompts import PromptNotFound, reset_store_cache

client = TestClient(app_module.app, raise_server_exceptions=False)


class _EmptyStore:
    def get(self, key):
        raise PromptNotFound(key)


def test_an_unpublished_planner_prompt_is_a_502_naming_the_key():
    reset_store_cache()  # a fresh store with no snapshot, as when no database is configured

    resp = client.post("/plan/email", json={"segment_context": {}, "objective": "x"})

    assert resp.status_code == 502
    assert "campaign.plan.instructions" in resp.json()["detail"]
    assert "not published" in resp.json()["detail"]


def test_an_unpublished_use_case_prompt_is_a_502_naming_the_key(monkeypatch):
    monkeypatch.setattr(ins, "get_store", lambda: _EmptyStore())

    resp = client.post(
        "/analytics/sql",
        json={"question": "how many segments", "schema": {"cdp_segments": "cdp_segments: segment_id"}},
    )

    assert resp.status_code == 502
    assert "analytics.sql.instructions" in resp.json()["detail"]
    assert "not published" in resp.json()["detail"]
