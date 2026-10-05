import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest

import ai_agents_runners.runner as runner
from ai_agents_runners.runner import AgentWorkflowMasterTask, WorkflowRunSummary


@pytest.fixture(autouse=True)
def recommendation_cache_invalidation(monkeypatch):
    invalidate = MagicMock()
    monkeypatch.setattr(runner.ContentRepository, "invalidate_recommendation_cache", invalidate)
    return invalidate


@pytest.mark.parametrize("dimensions", [384, 768])
def test_recommendation_model_key_allows_supported_dimensions(
    monkeypatch, dimensions
):
    monkeypatch.setenv("DOCS_EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("DOCS_GEMINI_EMBEDDING_DIMENSIONS", str(dimensions))

    assert AgentWorkflowMasterTask._embedding_model_key().endswith(f":{dimensions}")


def test_recommendation_model_key_rejects_unindexed_dimensions(monkeypatch):
    monkeypatch.setenv("DOCS_EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("DOCS_GEMINI_EMBEDDING_DIMENSIONS", "512")

    with pytest.raises(ValueError, match="384, 768"):
        AgentWorkflowMasterTask._embedding_model_key()


def test_recommendation_model_key_rejects_local_embedding_provider(monkeypatch):
    monkeypatch.setenv("DOCS_EMBEDDING_PROVIDER", "local")

    with pytest.raises(ValueError, match="openai.*gemini"):
        AgentWorkflowMasterTask._embedding_model_key()


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = None
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query, params):
        self.executed = (query, params)
        self.calls.append((query, params))

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows):
        self.cursor_instance = FakeCursor(rows)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self, **_):
        return self.cursor_instance


def test_api_trigger_selects_active_steps_in_database_order():
    rows = [
        {
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "segment_id": "22222222-2222-2222-2222-222222222222",
            "agent_code": "first",
            "model_type": "classification",
            "execution_order": 1,
            "configuration": {},
            "candidate_content_item_ids": [],
            "effective_schedule": None,
        }
    ]
    connection = FakeConnection(rows)
    summary = AgentWorkflowMasterTask(lambda: connection).run(
        trigger="api",
        tenant_id=rows[0]["tenant_id"],
        segment_id=rows[0]["segment_id"],
        trigger_event={"event_name": "profile.updated"},
    )
    assert summary.selected_steps == 1
    assert summary.plan[0]["trigger_event"]["event_name"] == "profile.updated"
    assert connection.cursor_instance.executed[1] == [
        rows[0]["tenant_id"],
        rows[0]["segment_id"],
    ]


def test_candidate_uuid_array_is_normalized_before_pipeline_input():
    tenant_id = "11111111-1111-1111-1111-111111111111"
    segment_id = "22222222-2222-2222-2222-222222222222"
    candidate_ids = [
        "33333333-3333-3333-3333-333333333333",
        "44444444-4444-4444-4444-444444444444",
    ]
    row = {
        "tenant_id": tenant_id,
        "segment_id": segment_id,
        "agent_code": "product_recommendation",
        "model_type": "ranking_recommendation",
        "execution_order": 1,
        "configuration": {},
        "candidate_content_item_ids": json.dumps(candidate_ids),
        "effective_schedule": None,
    }

    connection = FakeConnection([row])
    summary = AgentWorkflowMasterTask(lambda: connection).run(
        trigger="api",
        tenant_id=tenant_id,
        segment_id=segment_id,
    )

    assert summary.plan[0]["candidate_content_item_ids"] == candidate_ids
    assert "to_json(w.candidate_content_item_ids)" in connection.cursor_instance.executed[0]


def test_cron_trigger_skips_steps_that_are_not_due():
    rows = [
        {
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "segment_id": "22222222-2222-2222-2222-222222222222",
            "agent_code": "daily",
            "model_type": "classification",
            "execution_order": 1,
            "configuration": {},
            "candidate_content_item_ids": [],
            "effective_schedule": "0 2 * * *",
        }
    ]
    summary = AgentWorkflowMasterTask(lambda: FakeConnection(rows)).run(
        trigger="cron",
        scheduled_at=datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc),
    )
    assert summary.selected_steps == 0
    assert summary.skipped_steps == 1


def test_execute_ranking_step_persists_results_for_every_segment_profile(
    monkeypatch, recommendation_cache_invalidation
):
    tenant_id = "11111111-1111-1111-1111-111111111111"
    segment_id = "22222222-2222-2222-2222-222222222222"
    content_id = "33333333-3333-3333-3333-333333333333"
    profiles = [
        {
            "master_profile_id": "44444444-4444-4444-4444-444444444444",
            "domain": "retail",
            "segmentation_tags": ["loyal", "vip"],
        },
        {
            "master_profile_id": "55555555-5555-5555-5555-555555555555",
            "domain": "retail",
            "segmentation_tags": ["loyal"],
        },
    ]
    connections = []

    def connection_factory():
        connection = FakeConnection(profiles if not connections else [])
        connections.append(connection)
        return connection

    pipeline_payloads = []
    persisted_rows = []

    def pipeline_executor(payload, run_id):
        pipeline_payloads.append((payload, run_id))
        return SimpleNamespace(
            result={
                "ranked_items": [
                    {
                        "item_id": content_id,
                        "rank": 1,
                        "score": 1,
                        "matched_tags": ["loyal"],
                        "reason": "segment_tag_overlap",
                    }
                ]
            }
        )

    monkeypatch.setattr(
        runner,
        "execute_values",
        lambda _cursor, _query, rows, **_kwargs: persisted_rows.append(list(rows)),
    )
    task = AgentWorkflowMasterTask(
        connection_factory=connection_factory,
        pipeline_executor=pipeline_executor,
    )
    summary = WorkflowRunSummary(
        trigger="api",
        tenant_id=tenant_id,
        segment_id=segment_id,
        selected_steps=1,
        skipped_steps=0,
        plan=(
            {
                "tenant_id": tenant_id,
                "segment_id": segment_id,
                "agent_code": "product_recommendation",
                "model_type": "ranking_recommendation",
                "execution_order": 1,
                "configuration": {"limit": 4},
                "candidate_content_item_ids": [content_id],
                "trigger_event": {"event_name": "manual"},
            },
        ),
    )

    result = task.execute(summary, run_id="dagster-run-1")

    assert result.executed_steps == 1
    assert result.profile_runs_processed == 2
    assert result.recommendations_written == 2
    assert result.unsupported_steps == 0
    assert [payload[0]["input_data"]["segmentation_tags"] for payload in pipeline_payloads] == [
        ["loyal", "vip"],
        ["loyal"],
    ]
    assert all(payload[1] == "dagster-run-1" for payload in pipeline_payloads)
    assert [rows[0][2] for rows in persisted_rows] == [
        profiles[0]["master_profile_id"],
        profiles[1]["master_profile_id"],
    ]
    assert all(rows[0][4] == content_id for rows in persisted_rows)
    assert connections[0].cursor_instance.calls[-1][1] == (segment_id, tenant_id)
    assert connections[1].cursor_instance.calls[-1][1] == (
        tenant_id,
        segment_id,
        "dagster-run-1",
        len(profiles),
        1,
    )
    assert connections[-1].cursor_instance.calls[1][1][0] == "SUCCEEDED"
    recommendation_cache_invalidation.assert_called_once_with(UUID(tenant_id))


def test_failed_recommendation_run_is_not_published(recommendation_cache_invalidation):
    tenant_id = "11111111-1111-1111-1111-111111111111"
    segment_id = "22222222-2222-2222-2222-222222222222"
    content_id = "33333333-3333-3333-3333-333333333333"
    profile = {
        "master_profile_id": "44444444-4444-4444-4444-444444444444",
        "domain": "retail",
        "segmentation_tags": ["loyal"],
    }
    connections = []

    def connection_factory():
        connection = FakeConnection([profile] if not connections else [])
        connections.append(connection)
        return connection

    def fail_pipeline(_payload, _run_id):
        raise ValueError("candidate ranking failed")

    task = AgentWorkflowMasterTask(
        connection_factory=connection_factory,
        pipeline_executor=fail_pipeline,
    )
    summary = WorkflowRunSummary(
        trigger="api",
        tenant_id=tenant_id,
        segment_id=segment_id,
        selected_steps=1,
        skipped_steps=0,
        plan=(
            {
                "tenant_id": tenant_id,
                "segment_id": segment_id,
                "agent_code": "product_recommendation",
                "model_type": "ranking_recommendation",
                "execution_order": 1,
                "configuration": {},
                "candidate_content_item_ids": [content_id],
                "trigger_event": {},
            },
        ),
    )

    with pytest.raises(ValueError, match="candidate ranking failed") as error:
        task.execute(summary, run_id="dagster-run-failed")

    assert f"profile={profile['master_profile_id']}" in str(error.value)
    assert f"tenant={tenant_id}" in str(error.value)
    assert f"segment={segment_id}" in str(error.value)
    assert "agent=product_recommendation" in str(error.value)
    finish_calls = connections[-1].cursor_instance.calls
    assert finish_calls[1][1][0] == "FAILED"
    assert finish_calls[2][1] == (tenant_id, segment_id, "dagster-run-failed")
    recommendation_cache_invalidation.assert_not_called()


def test_hybrid_workflow_embeds_candidates_and_profile_queries(monkeypatch):
    monkeypatch.setenv("DOCS_EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("DOCS_GEMINI_EMBEDDING_DIMENSIONS", "768")
    tenant_id = "11111111-1111-1111-1111-111111111111"
    segment_id = "22222222-2222-2222-2222-222222222222"
    profile = {
        "master_profile_id": "44444444-4444-4444-4444-444444444444",
        "domain": "retail",
        "segmentation_tags": ["loyal", "books"],
    }
    candidate_id = "33333333-3333-3333-3333-333333333333"
    candidate = {
        "content_item_id": candidate_id,
        "domain": "retail",
        "item_type": "product",
        "title": "Book",
        "summary": "A travel guide",
        "segment_tags": ["books"],
        "embedding_text": None,
        "embedding_model": None,
        "embedding_version": None,
        "embedding_dimensions": None,
    }
    connection_rows = [[profile], [], [candidate], [], [], [], []]
    connections = []

    def connection_factory():
        connection = FakeConnection(connection_rows[len(connections)])
        connections.append(connection)
        return connection

    embedded_batches = []
    def embedder(texts, *, task):
        embedded_batches.append((list(texts), task))
        return [[0.1] * 768 for _ in texts]

    execute_values_calls = []
    monkeypatch.setattr(
        runner,
        "execute_values",
        lambda _cursor, query, rows, **_kwargs: execute_values_calls.append(
            (query, list(rows))
        ),
    )
    pipeline_calls = []

    def pipeline_executor(payload, run_id):
        pipeline_calls.append((payload, run_id))
        return SimpleNamespace(
            result={
                "ranked_items": [
                    {
                        "item_id": candidate_id,
                        "rank": 1,
                        "score": 0.9,
                        "semantic_score": 0.95,
                        "tag_score": 0.7,
                        "strategy": "hybrid",
                        "matched_tags": ["books"],
                        "reason": "semantic_and_segment_tag_match",
                    }
                ]
            }
        )

    task = AgentWorkflowMasterTask(
        connection_factory=connection_factory,
        pipeline_executor=pipeline_executor,
        embedding_function=embedder,
    )
    summary = WorkflowRunSummary(
        trigger="api",
        tenant_id=tenant_id,
        segment_id=segment_id,
        selected_steps=1,
        skipped_steps=0,
        plan=(
            {
                "tenant_id": tenant_id,
                "segment_id": segment_id,
                "agent_code": "product_recommendation",
                "model_type": "ranking_recommendation",
                "execution_order": 1,
                "configuration": {
                    "strategy": "hybrid",
                    "semantic_query": "prefer travel reading",
                },
                "candidate_content_item_ids": [candidate_id],
                "trigger_event": {},
            },
        ),
    )

    result = task.execute(summary, run_id="dagster-run-vector")

    assert result.recommendations_written == 1
    assert [batch[1] for batch in embedded_batches] == ["document", "query"]
    assert "Title: Book." in embedded_batches[0][0][0]
    assert "prefer travel reading" in embedded_batches[1][0][0]
    assert "profile_embedding" in pipeline_calls[0][0]["input_data"]
    assert pipeline_calls[0][0]["configuration"]["strategy"] == "hybrid"
    assert len(execute_values_calls) == 2
    assert execute_values_calls[0][0] == runner.UPDATE_CONTENT_EMBEDDINGS_SQL
    assert execute_values_calls[0][1][0][1] == candidate_id
    assert execute_values_calls[0][1][0][3] == "gemini:gemini-embedding-001:768"
    assert execute_values_calls[0][1][0][4] == "Type: product. Domain: retail. Title: Book. Summary: A travel guide Keywords: books."
    assert execute_values_calls[0][1][0][5] == "1"
    assert execute_values_calls[1][1][0][8:11] == (0.95, 0.7, "hybrid")
