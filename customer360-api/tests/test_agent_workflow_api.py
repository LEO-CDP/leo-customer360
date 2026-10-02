"""Unit tests for tenant-scoped segment agent workflow endpoints."""

import uuid
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import require_tenant
from core.database import get_db
from core.routers.agent_workflow_api import router
from core.repositories.agent_workflow_repository import (
    AgentWorkflowConflictError,
    AgentWorkflowNotFoundError,
    AgentWorkflowValidationError,
)


TENANT_ID = "11111111-1111-1111-1111-111111111111"
SEGMENT_ID = "22222222-2222-2222-2222-222222222222"
WORKFLOW_ID = "33333333-3333-3333-3333-333333333333"
CONTENT_ID = "44444444-4444-4444-4444-444444444444"


def step(**overrides):
    value = {
        "agent_workflow_id": WORKFLOW_ID,
        "tenant_id": TENANT_ID,
        "segment_id": SEGMENT_ID,
        "agent_code": "recommendation",
        "execution_order": 1,
        "is_active": True,
        "schedule_definition": "0 2 * * *",
        "candidate_content_item_ids": [CONTENT_ID],
        "configuration": {"top_k": 5},
        "agent_display_name": "Recommendation Agent",
        "agent_model_type": "generative_llm",
        "agent_status": "ACTIVE",
        "created_at": None,
        "updated_at": None,
    }
    value.update(overrides)
    return value


def make_client(repo=None):
    app = FastAPI()
    app.include_router(router)
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_tenant] = lambda: TENANT_ID
    return TestClient(app), repo or MagicMock()


def test_list_workflow_is_tenant_scoped_and_ordered():
    client, repo = make_client()
    repo.list_steps.return_value = [step()]
    with patch("core.routers.agent_workflow_api.AgentWorkflowRepository", return_value=repo):
        response = client.get(f"/segments/{SEGMENT_ID}/workflow")
    assert response.status_code == 200
    assert response.json()[0]["agent_code"] == "recommendation"
    repo.list_steps.assert_called_once_with(uuid.UUID(TENANT_ID), uuid.UUID(SEGMENT_ID))


def test_replace_workflow_accepts_ordered_steps_without_tenant_body_field():
    client, repo = make_client()
    repo.replace_steps.return_value = [step()]
    with patch("core.routers.agent_workflow_api.AgentWorkflowRepository", return_value=repo):
        response = client.put(
            f"/segments/{SEGMENT_ID}/workflow",
            json={
                "steps": [
                    {
                        "agent_code": "recommendation",
                        "execution_order": 1,
                        "candidate_content_item_ids": [CONTENT_ID],
                        "configuration": {"top_k": 5},
                    }
                ]
            },
        )
    assert response.status_code == 200
    repo.replace_steps.assert_called_once()
    assert repo.replace_steps.call_args.args[0] == uuid.UUID(TENANT_ID)
    assert repo.replace_steps.call_args.args[2][0]["agent_code"] == "recommendation"


def test_replace_workflow_rejects_duplicate_candidates_before_repository():
    client, repo = make_client()
    with patch("core.routers.agent_workflow_api.AgentWorkflowRepository", return_value=repo):
        response = client.put(
            f"/segments/{SEGMENT_ID}/workflow",
            json={
                "steps": [
                    {
                        "agent_code": "recommendation",
                        "execution_order": 1,
                        "candidate_content_item_ids": [CONTENT_ID, CONTENT_ID],
                    }
                ]
            },
        )
    assert response.status_code == 422
    repo.replace_steps.assert_not_called()


def test_replace_workflow_accepts_cron_override_and_rejects_invalid_schedule():
    client, repo = make_client()
    repo.replace_steps.return_value = [step()]
    with patch("core.routers.agent_workflow_api.AgentWorkflowRepository", return_value=repo):
        response = client.put(
            f"/segments/{SEGMENT_ID}/workflow",
            json={
                "steps": [
                    {
                        "agent_code": "recommendation",
                        "execution_order": 1,
                        "schedule_definition": " @daily ",
                    }
                ]
            },
        )
    assert response.status_code == 200
    assert repo.replace_steps.call_args.args[2][0]["schedule_definition"] == "@daily"

    with patch("core.routers.agent_workflow_api.AgentWorkflowRepository", return_value=repo):
        response = client.put(
            f"/segments/{SEGMENT_ID}/workflow",
            json={
                "steps": [
                    {
                        "agent_code": "recommendation",
                        "execution_order": 1,
                        "schedule_definition": "every morning",
                    }
                ]
            },
        )
    assert response.status_code == 422
    assert repo.replace_steps.call_count == 1


def test_workflow_repository_errors_map_to_client_statuses():
    client, repo = make_client()
    for error, expected_status in (
        (AgentWorkflowNotFoundError("missing"), 404),
        (AgentWorkflowValidationError("bad reference"), 422),
        (AgentWorkflowConflictError("duplicate"), 409),
    ):
        repo.list_steps.side_effect = error
        with patch("core.routers.agent_workflow_api.AgentWorkflowRepository", return_value=repo):
            response = client.get(f"/segments/{SEGMENT_ID}/workflow")
        assert response.status_code == expected_status
        repo.list_steps.side_effect = None


def test_workflow_requires_tenant_context():
    client, repo = make_client()
    client.app.dependency_overrides[require_tenant] = lambda: (_ for _ in ()).throw(
        __import__("fastapi").HTTPException(status_code=400, detail="No tenant context found")
    )
    with patch("core.routers.agent_workflow_api.AgentWorkflowRepository", return_value=repo):
        response = client.get(f"/segments/{SEGMENT_ID}/workflow")
    assert response.status_code == 400
