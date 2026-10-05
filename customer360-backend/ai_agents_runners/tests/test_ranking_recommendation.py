from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from ai_agents_runners.agent_pipeline.agent_types.ranking_recommendation import (
    RANK_CANDIDATE_CONTENT_SQL,
    RankingRecommendationPipeline,
)
from ai_agents_runners.agent_pipeline.pipelines import PIPELINE_HANDLERS
from ai_agents_runners.agent_pipeline.pipelines import execute_agent_pipeline

TENANT_ID = "11111111-1111-1111-1111-111111111111"
SEGMENT_ID = "22222222-2222-2222-2222-222222222222"
CONTENT_ITEM_1 = "33333333-3333-3333-3333-333333333333"
CONTENT_ITEM_2 = "44444444-4444-4444-4444-444444444444"


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.query = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query, params):
        self.query = query
        self.params = params

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


def _input(**overrides):
    payload = {
        "tenant_id": TENANT_ID,
        "segment_id": SEGMENT_ID,
        "agent_code": "product_recommendation",
        "model_type": "ranking_recommendation",
        "input_data": {
            "domain": "retail",
            "segmentation_tags": ["loyal", "vip"],
        },
        "candidate_content_item_ids": [CONTENT_ITEM_1, CONTENT_ITEM_2],
    }
    payload.update(overrides)
    return payload


def _candidate(content_item_id, *, score, matched_tags, published_at):
    return {
        "content_item_id": UUID(content_item_id),
        "item_type": "product",
        "title": f"Product {content_item_id[-1]}",
        "summary": "A recommended product",
        "image_url": None,
        "cta_label": "View",
        "cta_url": "/products/example",
        "published_at": published_at,
        "matched_tags": matched_tags,
        "score": score,
    }


def test_ranking_pipeline_scores_and_returns_explicit_candidates_in_order(monkeypatch):
    newer = datetime(2026, 10, 5, tzinfo=timezone.utc)
    connection = FakeConnection(
        [
            _candidate(CONTENT_ITEM_1, score=2, matched_tags=["loyal", "vip"], published_at=newer),
            _candidate(CONTENT_ITEM_2, score=1, matched_tags=["vip"], published_at=newer),
        ]
    )
    pipeline = RankingRecommendationPipeline(connection_factory=lambda: connection)
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)

    output = execute_agent_pipeline(
        _input(configuration={"limit": 1}),
        run_id="dagster-run-1",
    )

    assert output.model_type == "ranking_recommendation"
    assert output.tenant_id == UUID(TENANT_ID)
    assert output.result["ranked_items"] == [
        {
            "item_id": CONTENT_ITEM_1,
            "rank": 1,
            "score": 2,
            "matched_tags": ["loyal", "vip"],
            "reason": "segment_tag_overlap",
            "item_type": "product",
            "title": "Product 3",
            "summary": "A recommended product",
            "image_url": None,
            "cta_label": "View",
            "cta_url": "/products/example",
            "published_at": newer.isoformat(),
        }
    ]

    query = connection.cursor_instance.query
    assert query == RANK_CANDIDATE_CONTENT_SQL
    assert "content.tenant_id = %s" in query
    assert "content.status_code = 1" in query
    assert "(content.domain = 'all' OR content.domain = %s)" in query
    assert "content.content_item_id = ANY(%s::uuid[])" in query
    assert "ORDER BY score DESC, content.published_at DESC NULLS LAST" in query
    assert connection.cursor_instance.params == (
        ["loyal", "vip"],
        TENANT_ID,
        "retail",
        [CONTENT_ITEM_1, CONTENT_ITEM_2],
    )


def test_empty_candidate_list_returns_empty_without_loading_catalog(monkeypatch):
    def unexpected_database_access():
        pytest.fail("empty candidate list must not load the content catalog")

    pipeline = RankingRecommendationPipeline(connection_factory=unexpected_database_access)
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)
    payload = _input(candidate_content_item_ids=[])

    output = execute_agent_pipeline(payload, run_id="dagster-run-1")

    assert output.result == {"ranked_items": []}


def test_ranking_pipeline_rejects_candidate_not_eligible_for_tenant(monkeypatch):
    connection = FakeConnection([])
    pipeline = RankingRecommendationPipeline(connection_factory=lambda: connection)
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)

    with pytest.raises(ValueError, match="unavailable for this tenant"):
        execute_agent_pipeline(_input(), run_id="dagster-run-1")


def test_ranking_pipeline_uses_fallback_reason_without_tag_overlap(monkeypatch):
    connection = FakeConnection(
        [
            _candidate(
                CONTENT_ITEM_1,
                score=0,
                matched_tags=[],
                published_at=None,
            )
        ]
    )
    pipeline = RankingRecommendationPipeline(connection_factory=lambda: connection)
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)

    output = execute_agent_pipeline(
        _input(candidate_content_item_ids=[CONTENT_ITEM_1]),
        run_id="dagster-run-1",
    )

    item = output.result["ranked_items"][0]
    assert item["score"] == 0
    assert item["reason"] == "no_segment_tag_overlap"
    assert item["published_at"] is None


def test_ranking_pipeline_propagates_database_errors(monkeypatch):
    def fail_connection():
        raise RuntimeError("database unavailable")

    pipeline = RankingRecommendationPipeline(connection_factory=fail_connection)
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)

    with pytest.raises(RuntimeError, match="database unavailable"):
        execute_agent_pipeline(_input(), run_id="dagster-run-1")


@pytest.mark.parametrize(
    "ranked_items",
    [
        [{"rank": 1, "score": 1, "matched_tags": []}],
        [{"item_id": CONTENT_ITEM_1, "rank": 1, "score": -1, "matched_tags": []}],
        [{"item_id": CONTENT_ITEM_1, "rank": 1.0, "score": 1, "matched_tags": []}],
        [
            {"item_id": CONTENT_ITEM_1, "rank": 1, "score": 1, "matched_tags": []},
            {"item_id": CONTENT_ITEM_1, "rank": 2, "score": 0, "matched_tags": []},
        ],
    ],
)
def test_ranking_output_contract_rejects_malformed_ranked_items(ranked_items):
    with pytest.raises(ValidationError):
        PIPELINE_HANDLERS["ranking_recommendation"].result_model.model_validate(
            {"ranked_items": ranked_items}
        )


@pytest.mark.parametrize(
    "input_data, configuration, message",
    [
        ({"segmentation_tags": ["vip"]}, {}, "domain"),
        ({"domain": "retail", "segmentation_tags": "vip"}, {}, "segmentation_tags"),
        ({"domain": "retail", "segmentation_tags": [1]}, {}, "segmentation_tags"),
        ({"domain": "retail", "segmentation_tags": []}, {"limit": True}, "positive integer"),
        ({"domain": "retail", "segmentation_tags": []}, {"limit": 0}, "positive integer"),
    ],
)
def test_ranking_pipeline_rejects_invalid_profile_or_limit(
    monkeypatch, input_data, configuration, message
):
    pipeline = RankingRecommendationPipeline(
        connection_factory=lambda: pytest.fail("database should not be queried")
    )
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)

    with pytest.raises(ValueError, match=message):
        execute_agent_pipeline(
            _input(input_data=input_data, configuration=configuration),
            run_id="dagster-run-1",
        )
