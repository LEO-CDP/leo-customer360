"""Tests for tenant-scoped campaign workflow save/load endpoints."""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from core.database import get_db
from core.repositories.campaign_workflow_repository import CampaignWorkflowRepository
from core.routers.workflow_api import router
from leo_customer360_dao.schemas.workflow import CampaignWorkflowSaveRequest
from leo_customer360_dao.workflow.repository import SavedFlow

TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
CAMPAIGN_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
FLOW_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
SEGMENT_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
USER_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")


def _campaign():
    return SimpleNamespace(
        campaign_id=CAMPAIGN_ID,
        tenant_id=TENANT_ID,
        name="Spring retention",
        workflow_id=FLOW_ID,
        workflow_version=3,
    )


def _payload():
    return {
        "campaign": {
            "tenant_id": str(TENANT_ID),
            "name": "Spring retention",
            "segment_id": str(SEGMENT_ID),
            "objective": "Conversions",
        },
        "workflow": {
            "schema_version": 2,
            "root": {
                "id": "n1",
                "type": "trigger",
                "config": {
                    "segment_id": str(SEGMENT_ID),
                    "event": "cart_abandoned",
                },
                "children": [],
            },
        },
    }


def _request_model():
    from leo_customer360_dao.schemas.crm import CampaignCreate
    from leo_customer360_dao.workflow.models import FlowDefinition

    data = _payload()
    return CampaignWorkflowSaveRequest(
        campaign=CampaignCreate.model_validate(data["campaign"]),
        workflow=FlowDefinition.model_validate(data["workflow"]),
    )


class FakeSession:
    def __init__(self):
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def add(self, value):
        self.added.append(value)

    def flush(self):
        for value in self.added:
            if getattr(value, "campaign_id", None) is None:
                value.campaign_id = CAMPAIGN_ID

    def commit(self):
        self.commits += 1

    def refresh(self, _value):
        return None

    def rollback(self):
        self.rollbacks += 1


def _client():
    app = FastAPI()
    app.include_router(router)

    @app.middleware("http")
    async def identity(request: Request, call_next):
        request.state.tenant_id = str(TENANT_ID)
        request.state.user_id = str(USER_ID)
        return await call_next(request)

    app.dependency_overrides[get_db] = lambda: object()
    return TestClient(app)


def test_create_saves_campaign_and_workflow_in_one_request():
    client = _client()
    db = object()
    client.app.dependency_overrides[get_db] = lambda: db
    repository = SimpleNamespace(create=lambda payload: _campaign())

    with patch("core.routers.workflow_api.CampaignWorkflowRepository", return_value=repository) as factory, patch(
        "core.routers.workflow_api.invalidate_prefix"
    ) as invalidate:
        response = client.post("/campaigns/with-workflow", json=_payload())

    assert response.status_code == 201
    assert response.json()["campaign"]["campaign_id"] == str(CAMPAIGN_ID)
    assert response.json()["workflow_id"] == str(FLOW_ID)
    assert response.json()["workflow_version"] == 3
    factory.assert_called_once_with(
        db,
        tenant_id=TENANT_ID,
        user_id=USER_ID,
    )
    invalidate.assert_called_once_with("crm_campaign")


def test_update_and_load_are_campaign_scoped():
    client = _client()
    workflow = {"schema_version": 2, "root": {"id": "n1", "type": "trigger"}}
    repository = SimpleNamespace(
        update=lambda campaign_id, payload: _campaign(),
        load=lambda campaign_id: (_campaign(), workflow),
    )

    with patch("core.routers.workflow_api.CampaignWorkflowRepository", return_value=repository), patch(
        "core.routers.workflow_api.invalidate_prefix"
    ):
        saved = client.patch(f"/campaigns/{CAMPAIGN_ID}/with-workflow", json=_payload())
        loaded = client.get(f"/campaigns/{CAMPAIGN_ID}/workflow")

    assert saved.status_code == 200
    assert saved.json()["campaign"]["campaign_id"] == str(CAMPAIGN_ID)
    assert loaded.status_code == 200
    assert loaded.json() == {
        "campaign_id": str(CAMPAIGN_ID),
        "workflow_id": str(FLOW_ID),
        "workflow_version": 3,
        "workflow": workflow,
    }


def test_repository_commits_campaign_and_first_workflow_version_once():
    db = FakeSession()
    repository = CampaignWorkflowRepository(db, TENANT_ID)
    segment = SimpleNamespace(tenant_id=TENANT_ID, is_active=True, status_code=1)

    with patch(
        "core.repositories.campaign_workflow_repository.SegmentRepository"
    ) as segment_repo, patch(
        "core.repositories.campaign_workflow_repository.WorkflowRepository"
    ) as workflow_repo:
        segment_repo.return_value.get_segment.return_value = segment
        workflow_repo.return_value.save.return_value = SavedFlow(flow_id=FLOW_ID, version=1)
        campaign = repository.create(_request_model())

    assert campaign.campaign_id == CAMPAIGN_ID
    assert campaign.workflow_id == FLOW_ID
    assert campaign.workflow_version == 1
    assert db.commits == 1
    assert db.rollbacks == 0


def test_repository_rolls_back_campaign_when_workflow_save_fails():
    db = FakeSession()
    repository = CampaignWorkflowRepository(db, TENANT_ID)
    segment = SimpleNamespace(tenant_id=TENANT_ID, is_active=True, status_code=1)

    with patch(
        "core.repositories.campaign_workflow_repository.SegmentRepository"
    ) as segment_repo, patch(
        "core.repositories.campaign_workflow_repository.WorkflowRepository"
    ) as workflow_repo:
        segment_repo.return_value.get_segment.return_value = segment
        workflow_repo.return_value.save.side_effect = RuntimeError("workflow storage failed")
        try:
            repository.create(_request_model())
        except RuntimeError as error:
            assert str(error) == "workflow storage failed"
        else:
            raise AssertionError("workflow write failure should be raised")

    assert db.commits == 0
    assert db.rollbacks == 1


def test_repository_updates_workflow_and_campaign_in_one_transaction():
    db = FakeSession()
    campaign = SimpleNamespace(
        campaign_id=CAMPAIGN_ID,
        tenant_id=TENANT_ID,
        segment_id=SEGMENT_ID,
        workflow_id=FLOW_ID,
        workflow_version=2,
        approval_status="Draft",
        updated_at=None,
    )
    repository = CampaignWorkflowRepository(db, TENANT_ID, USER_ID)
    segment = SimpleNamespace(tenant_id=TENANT_ID, is_active=True, status_code=1)

    class FakeDraftRepository:
        def __init__(self, _session):
            pass

        def get_campaign(self, _tenant_id, _campaign_id):
            return campaign

        def edit_draft(self, **fields):
            assert fields["commit"] is False
            campaign.workflow_id = fields["workflow_id"]
            campaign.workflow_version = fields["workflow_version"]
            campaign.objective = fields["objective"]
            return campaign

    with patch(
        "core.repositories.campaign_workflow_repository.SegmentRepository"
    ) as segment_repo, patch(
        "core.repositories.campaign_workflow_repository.WorkflowRepository"
    ) as workflow_repo, patch(
        "core.repositories.campaign_draft_repository.CampaignDraftRepository",
        FakeDraftRepository,
    ):
        segment_repo.return_value.get_segment.return_value = segment
        workflow_repo.return_value.load_workflow.return_value = {
            "schema_version": 2,
            "root": {"id": "n1", "type": "trigger", "config": {"event": "signup"}},
        }
        workflow_repo.return_value.save.return_value = SavedFlow(flow_id=FLOW_ID, version=3)
        updated = repository.update(CAMPAIGN_ID, _request_model())

    assert updated.workflow_version == 3
    assert updated.objective == "Conversions"
    assert db.commits == 1
    assert db.rollbacks == 0
