"""Tests for tenant-scoped campaign workflow save/load endpoints."""

import uuid
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from core.database import get_db
from core.repositories.campaign_draft_repository import CampaignDraftActorNotFoundError
from core.repositories.campaign_workflow_repository import CampaignWorkflowRepository
from core.routers.workflow_api import review_router, router
from core.routers.workflow_api import _raise_http_error
from leo_customer360_dao.schemas.workflow import CampaignWorkflowSaveRequest
from leo_customer360_dao.workflow import FlowNotFound, NodeDef, WorkflowRuleViolation
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
    def __init__(self, is_tenant_member=True):
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.is_tenant_member = is_tenant_member

    def execute(self, _statement):
        return SimpleNamespace(
            scalar_one_or_none=lambda: USER_ID if self.is_tenant_member else None
        )

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
    app.include_router(review_router)

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
        load=lambda campaign_id: (_campaign(), workflow, {"approval_status": "InReview"}),
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
        "approval_status": "InReview",
        "workflow": workflow,
    }


def test_load_without_linked_workflow_returns_null_governance_fields():
    client = _client()
    repository = SimpleNamespace(load=lambda campaign_id: (_campaign(), None, None))

    with patch("core.routers.workflow_api.CampaignWorkflowRepository", return_value=repository):
        loaded = client.get(f"/campaigns/{CAMPAIGN_ID}/workflow")

    assert loaded.status_code == 200
    assert loaded.json()["approval_status"] is None
    assert loaded.json()["workflow"] is None


def test_review_approves_version_as_tenant_admin_and_commits():
    client = _client()
    db = FakeSession()
    client.app.dependency_overrides[get_db] = lambda: db

    with patch("core.routers.workflow_api.require_tenant_admin") as admin_check, patch(
        "core.routers.workflow_api.WorkflowRepository"
    ) as workflow_repo:
        workflow_repo.return_value.review.return_value = "Approved"
        response = client.post(
            f"/workflows/{FLOW_ID}/versions/3/review",
            json={"decision": "approve", "reason": "Offer checked"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "workflow_id": str(FLOW_ID),
        "workflow_version": 3,
        "approval_status": "Approved",
    }
    admin_check.assert_called_once()
    workflow_repo.return_value.review.assert_called_once_with(
        FLOW_ID, 3, USER_ID, "approve", "Offer checked"
    )
    assert db.commits == 1
    assert db.rollbacks == 0


def test_review_is_forbidden_for_non_admin_and_never_touches_the_flow():
    client = _client()
    from fastapi import HTTPException

    with patch(
        "core.routers.workflow_api.require_tenant_admin",
        side_effect=HTTPException(status_code=403, detail="Tenant admin role required"),
    ), patch("core.routers.workflow_api.WorkflowRepository") as workflow_repo:
        response = client.post(
            f"/workflows/{FLOW_ID}/versions/3/review",
            json={"decision": "reject"},
        )

    assert response.status_code == 403
    workflow_repo.assert_not_called()


def test_review_maps_creator_self_approval_to_conflict_and_rolls_back():
    client = _client()
    db = FakeSession()
    client.app.dependency_overrides[get_db] = lambda: db

    with patch("core.routers.workflow_api.require_tenant_admin"), patch(
        "core.routers.workflow_api.WorkflowRepository"
    ) as workflow_repo:
        workflow_repo.return_value.review.side_effect = WorkflowRuleViolation(
            "reviewer must differ from the initiator"
        )
        response = client.post(
            f"/workflows/{FLOW_ID}/versions/3/review",
            json={"decision": "approve"},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "reviewer must differ from the initiator"
    assert db.commits == 0
    assert db.rollbacks == 1


def test_review_unknown_version_returns_404():
    client = _client()
    client.app.dependency_overrides[get_db] = lambda: FakeSession()

    with patch("core.routers.workflow_api.require_tenant_admin"), patch(
        "core.routers.workflow_api.WorkflowRepository"
    ) as workflow_repo:
        workflow_repo.return_value.review.side_effect = FlowNotFound("missing")
        response = client.post(
            f"/workflows/{FLOW_ID}/versions/9/review",
            json={"decision": "approve"},
        )

    assert response.status_code == 404


def test_review_rejects_unknown_decision():
    client = _client()
    response = client.post(
        f"/workflows/{FLOW_ID}/versions/3/review",
        json={"decision": "publish"},
    )
    assert response.status_code == 422


def test_repository_commits_campaign_and_first_workflow_version_once():
    db = FakeSession()
    repository = CampaignWorkflowRepository(db, TENANT_ID, USER_ID)
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
    assert campaign.user_id == USER_ID
    assert db.commits == 1
    assert db.rollbacks == 0


def test_create_ignores_client_supplied_owner_and_uses_session_user():
    db = FakeSession()
    repository = CampaignWorkflowRepository(db, TENANT_ID, USER_ID)
    segment = SimpleNamespace(tenant_id=TENANT_ID, is_active=True, status_code=1)
    payload = _request_model()
    payload.campaign.user_id = uuid.UUID("66666666-6666-6666-6666-666666666666")

    with patch(
        "core.repositories.campaign_workflow_repository.SegmentRepository"
    ) as segment_repo, patch(
        "core.repositories.campaign_workflow_repository.WorkflowRepository"
    ) as workflow_repo:
        segment_repo.return_value.get_segment.return_value = segment
        workflow_repo.return_value.save.return_value = SavedFlow(flow_id=FLOW_ID, version=1)
        campaign = repository.create(payload)

    assert campaign.user_id == USER_ID


def test_create_without_authenticated_user_is_refused_before_any_write():
    db = FakeSession()
    repository = CampaignWorkflowRepository(db, TENANT_ID)

    with patch("core.repositories.campaign_workflow_repository.WorkflowRepository") as workflow_repo:
        try:
            repository.create(_request_model())
        except CampaignDraftActorNotFoundError:
            pass
        else:
            raise AssertionError("create without a session user must be refused")

    workflow_repo.return_value.save.assert_not_called()
    assert db.added == []
    assert db.commits == 0


def test_create_refuses_user_outside_the_active_tenant():
    db = FakeSession(is_tenant_member=False)
    repository = CampaignWorkflowRepository(db, TENANT_ID, USER_ID)

    with patch("core.repositories.campaign_workflow_repository.WorkflowRepository") as workflow_repo:
        try:
            repository.create(_request_model())
        except CampaignDraftActorNotFoundError:
            pass
        else:
            raise AssertionError("a user outside the tenant must not own a campaign")

    workflow_repo.return_value.save.assert_not_called()
    assert db.added == []


@pytest.mark.parametrize(
    ("field", "operator", "value"),
    [
        ("engagement_score", "ge", "60"),
        ("churn_probability", "lt", "0.4"),
        ("lead_conversion_probability", "ge", "0.7"),
        ("historical_clv", "ge", "1000"),
        ("latest_nps_score", "ge", "8"),
        ("average_csat", "ge", "4"),
        ("predictive_clv", "ge", "2500"),
        ("overall_sentiment_score", "lt", "-0.2"),
        ("profile_completeness_score", "ge", "80"),
        ("identity_confidence_score", "ge", "0.9"),
        ("segmentation_tags", "has", "vip"),
    ],
)
def test_repository_accepts_supported_master_profile_condition_fields(field, operator, value):
    workflow = _request_model().workflow
    workflow.root.children = [
        NodeDef(
            id="n2",
            type="condition",
            config={"field": field, "op": operator, "value": value},
            children=[
                NodeDef(id="n3", type="exit", config={"kind": "goal"}, branch="yes"),
                NodeDef(id="n4", type="exit", config={"kind": "exit"}, branch="no"),
            ],
        )
    ]

    CampaignWorkflowRepository._validate_workflow(workflow, SEGMENT_ID)


def test_repository_rejects_operational_status_as_a_marketing_condition():
    workflow = _request_model().workflow
    workflow.root.children = [
        NodeDef(
            id="n2",
            type="condition",
            config={"field": "status_code", "op": "eq", "value": "1"},
            children=[
                NodeDef(id="n3", type="exit", config={"kind": "goal"}, branch="yes"),
                NodeDef(id="n4", type="exit", config={"kind": "exit"}, branch="no"),
            ],
        )
    ]

    with pytest.raises(ValueError, match="supported profile field"):
        CampaignWorkflowRepository._validate_workflow(workflow, SEGMENT_ID)


def test_repository_rolls_back_campaign_when_workflow_save_fails():
    db = FakeSession()
    repository = CampaignWorkflowRepository(db, TENANT_ID, USER_ID)
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


def test_edit_with_stale_identity_is_rejected_before_saving_workflow():
    db = FakeSession(is_tenant_member=False)
    repository = CampaignWorkflowRepository(db, TENANT_ID, USER_ID)
    with patch(
        "core.repositories.campaign_workflow_repository.WorkflowRepository"
    ) as workflows, pytest.raises(CampaignDraftActorNotFoundError, match="sign in again"):
        repository.update(CAMPAIGN_ID, _request_model())
    workflows.assert_not_called()
    assert db.commits == 0


def test_edit_without_signed_in_user_is_rejected_before_saving_workflow():
    db = FakeSession()
    with patch(
        "core.repositories.campaign_workflow_repository.WorkflowRepository"
    ) as workflows, pytest.raises(CampaignDraftActorNotFoundError):
        CampaignWorkflowRepository(db, TENANT_ID).update(CAMPAIGN_ID, _request_model())
    workflows.assert_not_called()


@pytest.mark.parametrize("constraint", ["crm_flow_version_creator_fk", "crm_flow_version_reviewer_fk"])
def test_stale_workflow_actor_foreign_key_returns_sign_in_error(constraint):
    original = SimpleNamespace(diag=SimpleNamespace(constraint_name=constraint), pgcode="23503")
    db = SimpleNamespace(rollback=Mock())
    with pytest.raises(HTTPException) as caught:
        _raise_http_error(IntegrityError("statement", {}, original), db)
    assert caught.value.status_code == 401
    assert "Sign out and sign in again" in caught.value.detail
    db.rollback.assert_called_once()


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

    assert workflow_repo.return_value.save.call_args.kwargs["user_id"] == USER_ID
    assert updated.workflow_version == 3
    assert updated.objective == "Conversions"
    assert db.commits == 1
    assert db.rollbacks == 0


def test_notification_requires_non_empty_body():
    workflow = _request_model().workflow
    workflow.root.children = [
        NodeDef(id="n2", type="notification", config={"provider": "sms", "body": " "}),
    ]
    with pytest.raises(ValueError, match="requires a message"):
        CampaignWorkflowRepository._validate_workflow(workflow, SEGMENT_ID)


def test_running_campaign_cannot_be_edited():
    from core.repositories.campaign_draft_repository import CampaignDraftConflictError

    campaign = SimpleNamespace(status="Running")
    with patch(
        "core.repositories.campaign_draft_repository.CampaignDraftRepository.get_campaign",
        return_value=campaign,
    ), pytest.raises(CampaignDraftConflictError, match="running campaign"):
        CampaignWorkflowRepository(FakeSession(), TENANT_ID, USER_ID).update(
            CAMPAIGN_ID, _request_model(),
        )


def test_actual_frontend_recipes_validate_and_execute_in_the_dao():
    """Check the production studio payloads against API and runtime contracts."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the frontend-to-runtime contract test")
    from leo_customer360_dao.models.identity import CdpMasterProfile
    from leo_customer360_dao.workflow import FlowDefinition, FlowEngine

    root = Path(__file__).resolve().parents[2]
    script = """
const fs = require('node:fs'), vm = require('node:vm');
const base = './customer360-frontend/static/js/workflow/';
const Core = require(base + 'workflow-core.js');
const context = { WorkflowCore: Core, Handlebars: {
  registerPartial() {}, compile() { return () => ''; }
}};
vm.runInNewContext(fs.readFileSync(base + 'workflow-template.js', 'utf8'), context);
const recipes = Object.values(context.WorkflowTemplates.RECIPES);
for (const recipe of recipes) {
  recipe.workflow.root.config.segment_id = process.argv[1];
  const errors = Core.validate(recipe.workflow).filter(issue => issue.level === 'error');
  if (errors.length) throw new Error(recipe.name + ': ' + JSON.stringify(errors));
}
console.log(JSON.stringify(recipes));
"""
    result = subprocess.run(
        [node, "-e", script, str(SEGMENT_ID)], cwd=root,
        capture_output=True, text=True, check=True, timeout=30,
    )
    recipes = json.loads(result.stdout)
    assert len(recipes) >= 26
    for recipe in recipes:
        definition = FlowDefinition.model_validate(recipe["workflow"])
        CampaignWorkflowRepository._validate_workflow(definition, SEGMENT_ID)
        for score in (0, 60, 100):
            profile = CdpMasterProfile(
                master_profile_id=uuid.uuid4(), tenant_id=TENANT_ID, first_name="UAT",
                engagement_score=score, communication_preferences={},
            )
            journey = FlowEngine(definition).run(profile)
            assert journey.end != "error", (recipe["name"], journey.error)


@pytest.mark.parametrize(
    ("constraint", "message"),
    [
        ("uq_crm_campaign_code", "Campaign code already exists for this tenant."),
        ("crm_flow_tenant_name_key", "workflow name already exists"),
        ("some_unmapped_constraint", "conflicts with existing data"),
    ],
)
def test_campaign_integrity_conflicts_return_safe_actionable_detail(constraint, message):
    diagnostic = SimpleNamespace(constraint_name=constraint, sqlstate="23505")
    original = SimpleNamespace(diag=diagnostic, pgcode="23505")
    error = IntegrityError("statement", {}, original)
    db = SimpleNamespace(rollback=Mock())

    with pytest.raises(HTTPException) as caught:
        _raise_http_error(error, db)

    assert caught.value.status_code == 409
    assert message in caught.value.detail
    db.rollback.assert_called_once()
