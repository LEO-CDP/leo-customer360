import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from ai_agents_runners.agent_pipeline.contracts import AgentPipelineInput
from ai_agents_runners.agent_pipeline.agent_types.ranking_recommendation import (
    CANDIDATE_DIAGNOSTICS_SQL,
    RANK_CANDIDATE_CONTENT_SQL,
    SUPPORTED_RECOMMENDATION_EMBEDDING_DIMENSIONS,
    CandidateDiagnostics,
    ProfileRankingOutcome,
    RankingExecutionState,
    RankingRecommendationPipeline,
    _ProfileRankingInput,
    _RankingSettings,
    _vector_literal,
    _weight,
)
from ai_agents_runners.agent_pipeline.pipelines import (
    PIPELINE_HANDLERS,
    execute_agent_pipeline,
    execute_agent_pipeline_batch,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
SEGMENT_ID = "22222222-2222-2222-2222-222222222222"
CONTENT_ITEM_1 = "33333333-3333-3333-3333-333333333333"
CONTENT_ITEM_2 = "44444444-4444-4444-4444-444444444444"


def test_semantic_score_normalizes_cosine_distance_to_unit_interval():
    expression = re.compile(
        r"1\.0 - \(\s*content\.embedding::vector\(__VECTOR_DIMENSIONS__\)"
        r"\s*<=>\s*profile_inputs\.profile_embedding\s*\)\s*/ 2\.0",
        re.DOTALL,
    )

    assert expression.search(RANK_CANDIDATE_CONTENT_SQL)


class FakeCursor:
    def __init__(self, rows, diagnostics=None):
        self.rows = rows
        self.diagnostics = diagnostics
        self.query: str = ""
        self.params: tuple[Any, ...] = ()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.query = query
        self.params = params

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.diagnostics


class FakeConnection:
    def __init__(self, rows, diagnostics=None):
        self.cursor_instance = FakeCursor(rows, diagnostics)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self, **_):
        return self.cursor_instance


def _input(**overrides: Any) -> dict[str, Any]:
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


def _candidate(
    content_item_id: str,
    *,
    score: float,
    matched_tags: list[str],
    published_at: datetime | None,
    profile_index: int = 0,
    rank: int = 1,
) -> dict[str, Any]:
    return {
        "profile_index": profile_index,
        "rank": rank,
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
        "semantic_score": 0.0,
        "tag_score": score,
        "strategy": "tags",
    }


def test_ranking_pipeline_scores_and_returns_explicit_candidates_in_order(monkeypatch):
    newer = datetime(2026, 10, 5, tzinfo=timezone.utc)
    connection = FakeConnection(
        [
            _candidate(
                CONTENT_ITEM_1,
                score=1,
                matched_tags=["loyal", "vip"],
                published_at=newer,
            ),
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
            "score": 1,
            "semantic_score": 0,
            "tag_score": 1,
            "strategy": "tags",
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
    assert query == RANK_CANDIDATE_CONTENT_SQL.replace(
        "__VECTOR_DIMENSIONS__", "384"
    )
    assert "content.tenant_id = criteria.tenant_id" in query
    assert "content.status_code = 1" in query
    assert "content.domain = 'all' OR content.domain = profile_inputs.domain" in query
    assert "content.content_item_id = ANY(criteria.candidate_ids)" in query
    assert "PARTITION BY profile_index" in query
    assert "ORDER BY score DESC, published_at DESC NULLS LAST, content_item_id" in query
    assert "WHERE rank <= top_k" in query
    assert connection.cursor_instance.params == (
        "tags",
        0.0,
        1.0,
        "",
        0.0,
        TENANT_ID,
        [CONTENT_ITEM_1, CONTENT_ITEM_2],
        1,
        json.dumps(
            [
                {
                    "profile_index": 0,
                    "domain": "retail",
                    "segmentation_tags": ["loyal", "vip"],
                    "profile_embedding": None,
                }
            ],
            separators=(",", ":"),
        ),
    )


def test_empty_candidate_list_returns_empty_without_loading_catalog(monkeypatch):
    def unexpected_database_access():
        pytest.fail("empty candidate list must not load the content catalog")

    pipeline = RankingRecommendationPipeline(connection_factory=unexpected_database_access)
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)
    payload = _input(candidate_content_item_ids=[])

    output = execute_agent_pipeline(payload, run_id="dagster-run-1")

    assert output.result == {"ranked_items": []}


def test_batch_ranking_scores_profiles_in_one_query(monkeypatch):
    newer = datetime(2026, 10, 5, tzinfo=timezone.utc)
    connection = FakeConnection(
        [
            _candidate(
                CONTENT_ITEM_1,
                score=1,
                matched_tags=["loyal"],
                published_at=newer,
                profile_index=0,
            ),
            _candidate(
                CONTENT_ITEM_2,
                score=0.5,
                matched_tags=["vip"],
                published_at=None,
                profile_index=1,
            ),
        ]
    )
    pipeline = RankingRecommendationPipeline(connection_factory=lambda: connection)
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)
    first = _input(
        input_data={
            "master_profile_id": "55555555-5555-5555-5555-555555555555",
            "domain": "retail",
            "segmentation_tags": ["loyal"],
        }
    )
    second = _input(
        input_data={
            "master_profile_id": "66666666-6666-6666-6666-666666666666",
            "domain": "retail",
            "segmentation_tags": ["vip"],
        }
    )

    outputs = execute_agent_pipeline_batch(
        [first, second],
        run_id="dagster-run-batch",
    )

    assert [output.result["ranked_items"][0]["item_id"] for output in outputs] == [
        CONTENT_ITEM_1,
        CONTENT_ITEM_2,
    ]
    assert [output.result["ranked_items"][0]["rank"] for output in outputs] == [1, 1]
    assert connection.cursor_instance.query == RANK_CANDIDATE_CONTENT_SQL.replace(
        "__VECTOR_DIMENSIONS__", "384"
    )
    profiles_json = json.loads(connection.cursor_instance.params[-1])
    assert [profile["profile_index"] for profile in profiles_json] == [0, 1]
    assert [profile["segmentation_tags"] for profile in profiles_json] == [
        ["loyal"],
        ["vip"],
    ]


def test_maximum_sized_ranking_batch_handles_one_thousand_candidates(monkeypatch):
    candidate_ids = [CONTENT_ITEM_1] + [
        str(UUID(int=index + 1)) for index in range(999)
    ]
    batch_size = 100
    connection = FakeConnection(
        [
            _candidate(
                CONTENT_ITEM_1,
                score=0.8,
                matched_tags=["loyal"],
                published_at=None,
                profile_index=profile_index,
            )
            for profile_index in range(batch_size)
        ]
    )
    pipeline = RankingRecommendationPipeline(connection_factory=lambda: connection)
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)
    payloads = [
        _input(
            input_data={
                "master_profile_id": str(UUID(int=10_000 + profile_index)),
                "domain": "retail",
                "segmentation_tags": ["loyal"],
            },
            candidate_content_item_ids=candidate_ids,
        )
        for profile_index in range(batch_size)
    ]

    outputs = execute_agent_pipeline_batch(payloads, run_id="dagster-run-large-batch")

    assert len(outputs) == batch_size
    assert len(connection.cursor_instance.params[6]) == 1000
    assert len(json.loads(connection.cursor_instance.params[-1])) == batch_size
    assert "ROW_NUMBER() OVER" in connection.cursor_instance.query


def test_ranking_pipeline_rejects_profiles_over_batch_limit_without_database_access():
    pipeline = RankingRecommendationPipeline(
        connection_factory=lambda: pytest.fail("batch limit must be checked first")
    )
    payload = AgentPipelineInput.model_validate(_input())

    with pytest.raises(ValueError, match="must not exceed 100 profiles"):
        pipeline.process_batch([payload] * 101)


def test_ranking_pipeline_rejects_candidate_not_eligible_for_tenant(monkeypatch):
    connection = FakeConnection(
        [],
        diagnostics={
            "tenant_candidates": 0,
            "active_candidates": 0,
            "domain_candidates": 0,
            "rankable_candidates": 0,
        },
    )
    pipeline = RankingRecommendationPipeline(connection_factory=lambda: connection)
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)

    with pytest.raises(ValueError, match="unavailable for this tenant"):
        execute_agent_pipeline(_input(), run_id="dagster-run-1")


@pytest.mark.parametrize(
    ("counts", "reason"),
    [
        ((2, 0, 0, 0), "selected candidates are inactive"),
        ((2, 2, 2, 0), "selected candidates lack embeddings"),
        ((2, 2, 2, 2), "no candidate score meets minimum_score"),
    ],
)
def test_empty_rankings_explain_the_excluding_filter(monkeypatch, counts, reason):
    connection = FakeConnection(
        [],
        diagnostics=dict(
            zip(
                (
                    "tenant_candidates",
                    "active_candidates",
                    "domain_candidates",
                    "rankable_candidates",
                ),
                counts,
            )
        ),
    )
    pipeline = RankingRecommendationPipeline(connection_factory=lambda: connection)
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)

    with pytest.raises(ValueError, match=reason) as error:
        execute_agent_pipeline(
            _input(configuration={"minimum_score": 0.75}),
            run_id="dagster-run-1",
        )

    assert "minimum_score=0.75" in str(error.value)
    assert "domain=retail" in str(error.value)
    assert connection.cursor_instance.query == CANDIDATE_DIAGNOSTICS_SQL
    assert connection.cursor_instance.params[-4] == 384
    assert connection.cursor_instance.params[-2:] == (
        TENANT_ID,
        [CONTENT_ITEM_1, CONTENT_ITEM_2],
    )


def test_ranking_pipeline_skips_profiles_without_domain_matching_candidates(monkeypatch):
    connection = FakeConnection(
        [],
        diagnostics={
            "tenant_candidates": 2,
            "active_candidates": 2,
            "domain_candidates": 0,
            "rankable_candidates": 0,
        },
    )
    pipeline = RankingRecommendationPipeline(connection_factory=lambda: connection)
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)

    output = execute_agent_pipeline(_input(), run_id="dagster-run-1")

    assert output.result["ranked_items"] == []


def test_ranking_pipeline_uses_only_selected_candidates_available_for_profile_domain(
    monkeypatch,
):
    connection = FakeConnection(
        [
            _candidate(
                CONTENT_ITEM_1,
                score=0.5,
                matched_tags=["loyal"],
                published_at=None,
            )
        ]
    )
    pipeline = RankingRecommendationPipeline(connection_factory=lambda: connection)
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)

    output = execute_agent_pipeline(_input(), run_id="dagster-run-1")

    assert len(output.result["ranked_items"]) == 1
    assert output.result["ranked_items"][0]["item_id"] == CONTENT_ITEM_1


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


def test_hybrid_ranking_uses_pgvector_similarity_and_custom_weights(monkeypatch):
    monkeypatch.setenv("DOCS_EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("DOCS_GEMINI_EMBEDDING_DIMENSIONS", "768")
    query_vector = [0.01] * 768
    row = _candidate(
        CONTENT_ITEM_1,
        score=0.8,
        matched_tags=["loyal"],
        published_at=None,
    )
    row.update({"semantic_score": 0.9, "tag_score": 0.5, "strategy": "hybrid"})
    connection = FakeConnection([row])
    pipeline = RankingRecommendationPipeline(connection_factory=lambda: connection)
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)

    output = execute_agent_pipeline(
        _input(
            input_data={
                "domain": "retail",
                "segmentation_tags": ["loyal"],
                "profile_embedding": query_vector,
                "embedding_model": "gemini:gemini-embedding-001:768",
            },
            configuration={
                "strategy": "hybrid",
                "top_k": 3,
                "semantic_weight": 0.8,
                "tag_weight": 0.2,
            },
            candidate_content_item_ids=[CONTENT_ITEM_1],
        ),
        run_id="dagster-run-1",
    )

    assert output.result["ranked_items"][0]["strategy"] == "hybrid"
    assert output.result["ranked_items"][0]["semantic_score"] == 0.9
    assert (
        output.result["ranked_items"][0]["reason"]
        == "semantic_and_segment_tag_match"
    )
    assert "<=>" in connection.cursor_instance.query
    assert "::vector(768)" in connection.cursor_instance.query
    assert "vector" in connection.cursor_instance.query
    assert connection.cursor_instance.params[:4] == (
        "hybrid",
        0.8,
        0.2,
        "gemini:gemini-embedding-001:768",
    )
    assert connection.cursor_instance.params[7] == 3
    profile_inputs = json.loads(connection.cursor_instance.params[-1])
    assert profile_inputs[0]["profile_embedding"].startswith("[0.01,0.01")


@pytest.mark.parametrize("dimensions", sorted(SUPPORTED_RECOMMENDATION_EMBEDDING_DIMENSIONS))
def test_semantic_ranking_builds_query_for_supported_vector_sizes(monkeypatch, dimensions):
    embedding_model = f"gemini:gemini-embedding-001:{dimensions}"
    profile_embedding = [0.01] * dimensions
    row = _candidate(
        CONTENT_ITEM_1,
        score=0.8,
        matched_tags=["loyal"],
        published_at=None,
    )
    connection = FakeConnection([row])
    pipeline = RankingRecommendationPipeline(connection_factory=lambda: connection)
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)

    execute_agent_pipeline(
        _input(
            input_data={
                "domain": "retail",
                "segmentation_tags": ["loyal"],
                "profile_embedding": profile_embedding,
                "embedding_model": embedding_model,
            },
            configuration={"strategy": "semantic"},
            candidate_content_item_ids=[CONTENT_ITEM_1],
        ),
        run_id="dagster-run-1",
    )

    assert f"::vector({dimensions})" in connection.cursor_instance.query


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


@pytest.mark.parametrize("strategy", ["unknown", None, [], {"unexpected": "tags"}])
def test_ranking_pipeline_rejects_unsupported_strategies(monkeypatch, strategy):
    pipeline = RankingRecommendationPipeline(
        connection_factory=lambda: pytest.fail("database should not be queried")
    )
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)

    with pytest.raises(ValueError, match="configuration.strategy"):
        execute_agent_pipeline(
            _input(configuration={"strategy": strategy}),
            run_id="dagster-run-1",
        )


def test_ranking_pipeline_rejects_top_k_above_maximum(monkeypatch):
    pipeline = RankingRecommendationPipeline(
        connection_factory=lambda: pytest.fail("database should not be queried")
    )
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)

    with pytest.raises(ValueError, match="must not exceed 100"):
        execute_agent_pipeline(
            _input(configuration={"top_k": 101}),
            run_id="dagster-run-1",
        )


def test_hybrid_ranking_requires_a_positive_combined_weight(monkeypatch):
    pipeline = RankingRecommendationPipeline(
        connection_factory=lambda: pytest.fail("database should not be queried")
    )
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)

    with pytest.raises(ValueError, match="sum to more than zero"):
        execute_agent_pipeline(
            _input(
                configuration={
                    "strategy": "hybrid",
                    "semantic_weight": 0,
                    "tag_weight": 0,
                }
            ),
            run_id="dagster-run-1",
        )


@pytest.mark.parametrize("minimum_score", [None, True, "0.5", -0.1, 1.1])
def test_ranking_pipeline_rejects_invalid_minimum_score(monkeypatch, minimum_score):
    pipeline = RankingRecommendationPipeline(
        connection_factory=lambda: pytest.fail("database should not be queried")
    )
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)

    with pytest.raises(ValueError, match="minimum_score"):
        execute_agent_pipeline(
            _input(
                configuration={
                    "minimum_score": minimum_score,
                }
            ),
            run_id="dagster-run-1",
        )


@pytest.mark.parametrize("strategy", ["semantic", "hybrid"])
@pytest.mark.parametrize("profile_embedding", [None, "", []])
def test_vector_strategies_require_a_nonempty_profile_embedding(
    monkeypatch, strategy, profile_embedding
):
    pipeline = RankingRecommendationPipeline(
        connection_factory=lambda: pytest.fail("database should not be queried")
    )
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)

    with pytest.raises(ValueError, match="input_data.profile_embedding"):
        execute_agent_pipeline(
            _input(
                input_data={
                    "domain": "retail",
                    "segmentation_tags": [],
                    "profile_embedding": profile_embedding,
                    "embedding_model": "gemini:model:384",
                },
                configuration={"strategy": strategy},
            ),
            run_id="dagster-run-1",
        )


@pytest.mark.parametrize(
    "profile_embedding",
    [
        [True] * 384,
        ["0.1"] * 384,
        [10**1000] * 384,
    ],
)
def test_vector_strategies_reject_nonfinite_or_non_numeric_values(
    monkeypatch, profile_embedding
):
    pipeline = RankingRecommendationPipeline(
        connection_factory=lambda: pytest.fail("database should not be queried")
    )
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)

    with pytest.raises(ValueError, match="finite numbers"):
        execute_agent_pipeline(
            _input(
                input_data={
                    "domain": "retail",
                    "segmentation_tags": [],
                    "profile_embedding": profile_embedding,
                    "embedding_model": "gemini:model:384",
                },
                configuration={"strategy": "semantic"},
            ),
            run_id="dagster-run-1",
        )


@pytest.mark.parametrize("embedding_model", [None, "  "])
def test_vector_strategies_require_an_embedding_model_key(
    monkeypatch, embedding_model
):
    pipeline = RankingRecommendationPipeline(
        connection_factory=lambda: pytest.fail("database should not be queried")
    )
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)

    with pytest.raises(ValueError, match="input_data.embedding_model"):
        execute_agent_pipeline(
            _input(
                input_data={
                    "domain": "retail",
                    "segmentation_tags": [],
                    "profile_embedding": [0.1] * 384,
                    "embedding_model": embedding_model,
                },
                configuration={"strategy": "semantic"},
            ),
            run_id="dagster-run-1",
        )


@pytest.mark.parametrize(
    "embedding_model",
    ["local:model:384", ":384"],
)
def test_vector_strategies_reject_unsupported_embedding_providers(
    monkeypatch, embedding_model
):
    pipeline = RankingRecommendationPipeline(
        connection_factory=lambda: pytest.fail("database should not be queried")
    )
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)

    with pytest.raises(ValueError, match="supported provider"):
        execute_agent_pipeline(
            _input(
                input_data={
                    "domain": "retail",
                    "segmentation_tags": [],
                    "profile_embedding": [0.1] * 384,
                    "embedding_model": embedding_model,
                },
                configuration={"strategy": "semantic"},
            ),
            run_id="dagster-run-1",
        )


def test_vector_strategies_reject_unsupported_embedding_dimensions(monkeypatch):
    pipeline = RankingRecommendationPipeline(
        connection_factory=lambda: pytest.fail("database should not be queried")
    )
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)

    with pytest.raises(ValueError, match="384 or 768"):
        execute_agent_pipeline(
            _input(
                input_data={
                    "domain": "retail",
                    "segmentation_tags": [],
                    "profile_embedding": [0.1],
                    "embedding_model": "gemini:model:1",
                },
                configuration={"strategy": "semantic"},
            ),
            run_id="dagster-run-1",
        )


def test_vector_strategies_reject_malformed_model_dimensions(monkeypatch):
    pipeline = RankingRecommendationPipeline(
        connection_factory=lambda: pytest.fail("database should not be queried")
    )
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)

    with pytest.raises(ValueError, match="end with its vector dimension"):
        execute_agent_pipeline(
            _input(
                input_data={
                    "domain": "retail",
                    "segmentation_tags": [],
                    "profile_embedding": [0.1] * 384,
                    "embedding_model": "gemini:model:unknown",
                },
                configuration={"strategy": "semantic"},
            ),
            run_id="dagster-run-1",
        )


def test_vector_strategies_reject_model_vector_dimension_mismatch(monkeypatch):
    pipeline = RankingRecommendationPipeline(
        connection_factory=lambda: pytest.fail("database should not be queried")
    )
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)

    with pytest.raises(ValueError, match="dimension does not match"):
        execute_agent_pipeline(
            _input(
                input_data={
                    "domain": "retail",
                    "segmentation_tags": [],
                    "profile_embedding": [0.1] * 384,
                    "embedding_model": "gemini:model:768",
                },
                configuration={"strategy": "semantic"},
            ),
            run_id="dagster-run-1",
        )


@pytest.mark.parametrize(
    "value",
    [True, "0.5", float("nan"), float("inf"), 10**1000, -0.1, 1.1],
)
def test_weight_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="configuration.semantic_weight"):
        _weight({"semantic_weight": value}, "semantic_weight", 0.7)


def test_vector_literal_handles_none_and_numeric_vectors():
    assert _vector_literal(None) is None
    assert _vector_literal([0.5, 1]) == "[0.5,1.0]"


@pytest.mark.parametrize(
    ("semantic_score", "matched_tags", "expected_reason"),
    [
        (0.8, ["loyal"], "semantic_and_segment_tag_match"),
        (0.8, [], "semantic_similarity"),
        (0.0, ["loyal"], "segment_tag_overlap"),
        (None, [], "no_segment_tag_overlap"),
    ],
)
def test_serialization_selects_reason_from_score_and_tag_evidence(
    semantic_score, matched_tags, expected_reason
):
    row = _candidate(
        CONTENT_ITEM_1,
        score=0.5,
        matched_tags=matched_tags,
        published_at=None,
    )
    row.update(
        {
            "semantic_score": semantic_score,
            "tag_score": 0.5,
            "strategy": "hybrid",
        }
    )

    result = RankingRecommendationPipeline._serialize_candidate(row, rank=1)

    assert result["reason"] == expected_reason


def test_semantic_diagnostics_require_matching_vector_dimensions(monkeypatch):
    diagnostics = {
        "tenant_candidates": 1,
        "active_candidates": 1,
        "domain_candidates": 1,
        "rankable_candidates": 0,
    }
    connection = FakeConnection([], diagnostics=diagnostics)
    pipeline = RankingRecommendationPipeline(connection_factory=lambda: connection)
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)

    with pytest.raises(ValueError, match="lack embeddings"):
        execute_agent_pipeline(
            _input(
                input_data={
                    "domain": "retail",
                    "segmentation_tags": [],
                    "profile_embedding": [0.1] * 768,
                    "embedding_model": "gemini:model:768",
                },
                configuration={"strategy": "semantic"},
            ),
            run_id="dagster-run-1",
        )

    assert "vector_dims(embedding) = %s" in connection.cursor_instance.query
    assert connection.cursor_instance.params[-4:] == (
        768,
        "gemini:model:768",
        TENANT_ID,
        [CONTENT_ITEM_1, CONTENT_ITEM_2],
    )


def test_process_batch_empty_payloads_returns_empty_list():
    pipeline = RankingRecommendationPipeline(
        connection_factory=lambda: pytest.fail("database should not be queried")
    )
    assert pipeline.process_batch([]) == []
    assert pipeline.last_state is None


def test_process_batch_validates_candidate_limit_and_homogeneity():
    pipeline = RankingRecommendationPipeline(
        connection_factory=lambda: pytest.fail("database should not be queried")
    )
    first = AgentPipelineInput.model_validate(_input())

    over_limit = first.model_copy(
        update={
            "candidate_content_item_ids": [
                UUID(int=index + 1) for index in range(1001)
            ]
        }
    )
    with pytest.raises(ValueError, match="candidate_content_item_ids must not exceed"):
        pipeline.process_batch([over_limit])

    wrong_model = first.model_copy(update={"model_type": "classification"})
    with pytest.raises(ValueError, match="cannot handle classification"):
        pipeline.process_batch([wrong_model])

    mismatched_tenant = first.model_copy(
        update={"tenant_id": UUID("99999999-9999-9999-9999-999999999999")}
    )
    with pytest.raises(ValueError, match="must share tenant"):
        pipeline.process_batch([first, mismatched_tenant])


def test_process_batch_rejects_mixed_embedding_models_and_includes_profile_id_in_errors():
    pipeline = RankingRecommendationPipeline(
        connection_factory=lambda: pytest.fail("database should not be queried")
    )
    p1 = AgentPipelineInput.model_validate(
        _input(
            input_data={
                "master_profile_id": "55555555-5555-5555-5555-555555555555",
                "domain": "retail",
                "segmentation_tags": ["vip"],
                "profile_embedding": [0.1] * 384,
                "embedding_model": "gemini:model-a:384",
            },
            configuration={"strategy": "semantic"},
        )
    )
    p2 = AgentPipelineInput.model_validate(
        _input(
            input_data={
                "master_profile_id": "66666666-6666-6666-6666-666666666666",
                "domain": "retail",
                "segmentation_tags": ["vip"],
                "profile_embedding": [0.1] * 384,
                "embedding_model": "gemini:model-b:384",
            },
            configuration={"strategy": "semantic"},
        )
    )
    with pytest.raises(ValueError, match="must use one embedding model"):
        pipeline.process_batch([p1, p2])

    bad_profile = AgentPipelineInput.model_validate(
        _input(
            input_data={
                "master_profile_id": "77777777-7777-7777-7777-777777777777",
                "domain": "   ",
            }
        )
    )
    with pytest.raises(
        ValueError,
        match="profile=77777777-7777-7777-7777-777777777777, .*domain",
    ):
        pipeline.process_batch([bad_profile])


def test_vector_strategies_reject_missing_model_name_segment(monkeypatch):
    pipeline = RankingRecommendationPipeline(
        connection_factory=lambda: pytest.fail("database should not be queried")
    )
    monkeypatch.setitem(PIPELINE_HANDLERS, "ranking_recommendation", pipeline)

    for bad_key in ("gemini::384", "gemini:384", "openai:   :384"):
        with pytest.raises(ValueError, match="provider:model:dimensions"):
            execute_agent_pipeline(
                _input(
                    input_data={
                        "domain": "retail",
                        "segmentation_tags": [],
                        "profile_embedding": [0.1] * 384,
                        "embedding_model": bad_key,
                    },
                    configuration={"strategy": "semantic"},
                ),
                run_id="dagster-run-1",
            )


def test_ranking_result_contract_rejects_duplicate_item_ids_and_duplicate_ranks():
    valid_item = {
        "item_id": CONTENT_ITEM_1,
        "rank": 1,
        "score": 0.9,
        "strategy": "tags",
        "semantic_score": 0.0,
        "tag_score": 0.9,
        "matched_tags": ["loyal"],
        "reason": "segment_tag_overlap",
        "item_type": "product",
        "title": "Product 1",
        "summary": "Summary",
        "image_url": None,
        "cta_label": "View",
        "cta_url": "/p/1",
        "published_at": None,
    }
    duplicate_id_item = {**valid_item, "rank": 2}
    with pytest.raises(ValidationError, match="duplicate item_id"):
        PIPELINE_HANDLERS["ranking_recommendation"].result_model.model_validate(
            {"ranked_items": [valid_item, duplicate_id_item]}
        )

    duplicate_rank_item = {**valid_item, "item_id": CONTENT_ITEM_2, "rank": 1}
    with pytest.raises(ValidationError, match="duplicate rank"):
        PIPELINE_HANDLERS["ranking_recommendation"].result_model.model_validate(
            {"ranked_items": [valid_item, duplicate_rank_item]}
        )


def test_group_ranked_candidates_rejects_invalid_profile_index_and_rank():
    row_bad_index = _candidate(
        CONTENT_ITEM_1, score=0.5, matched_tags=[], published_at=None, profile_index=2
    )
    with pytest.raises(RuntimeError, match="invalid profile_index"):
        RankingRecommendationPipeline._group_ranked_candidates(
            rows=[row_bad_index], profile_count=1
        )

    row_bool_index = _candidate(
        CONTENT_ITEM_1,
        score=0.5,
        matched_tags=[],
        published_at=None,
        profile_index=True,  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="invalid profile_index"):
        RankingRecommendationPipeline._group_ranked_candidates(
            rows=[row_bool_index], profile_count=2
        )

    row_bad_rank = _candidate(
        CONTENT_ITEM_1,
        score=0.5,
        matched_tags=[],
        published_at=None,
        profile_index=0,
        rank=0,
    )
    with pytest.raises(RuntimeError, match="invalid rank"):
        RankingRecommendationPipeline._group_ranked_candidates(
            rows=[row_bad_rank], profile_count=1
        )


def test_candidate_diagnostics_and_no_candidates_helper_edge_cases():
    diag = CandidateDiagnostics(
        tenant_candidates=2,
        active_candidates=2,
        domain_candidates=0,
        rankable_candidates=0,
    )
    assert CandidateDiagnostics.from_mapping(diag) is diag
    assert len(diag) == 4
    assert list(iter(diag)) == [
        "tenant_candidates",
        "active_candidates",
        "domain_candidates",
        "rankable_candidates",
    ]
    assert diag["tenant_candidates"] == 2
    assert diag.is_domain_mismatch_only is True
    assert diag.failure_reason() == "selected candidates do not match the profile domain"

    connection = FakeConnection([], diagnostics=None)
    pipeline = RankingRecommendationPipeline(connection_factory=lambda: connection)
    profile = _ProfileRankingInput(
        profile_index=0,
        domain="retail",
        segmentation_tags=[],
        profile_embedding=None,
        embedding_model="",
        profile_id="prof-1",
    )
    settings = _RankingSettings(
        strategy="tags",
        top_k=8,
        semantic_weight=0.0,
        tag_weight=1.0,
        minimum_score=0.0,
        embedding_model="",
        dimensions=384,
    )
    with pytest.raises(RuntimeError, match="diagnostics returned no row"):
        pipeline._raise_no_candidates(
            tenant_id=UUID(TENANT_ID),
            candidate_ids=[UUID(CONTENT_ITEM_1)],
            profile=profile,
            settings=settings,
            counts=None,
        )


def test_state_observability_tracks_completed_short_circuited_and_failed_runs():
    observed_states: list[RankingExecutionState] = []
    newer = datetime(2026, 10, 5, tzinfo=timezone.utc)
    connection = FakeConnection(
        [
            _candidate(
                CONTENT_ITEM_1,
                score=1.0,
                matched_tags=["loyal"],
                published_at=newer,
                profile_index=0,
                rank=1,
            ),
        ],
        diagnostics={
            "tenant_candidates": 2,
            "active_candidates": 2,
            "domain_candidates": 0,
            "rankable_candidates": 0,
        },
    )
    pipeline = RankingRecommendationPipeline(
        connection_factory=lambda: connection,
        state_observer=observed_states.append,
    )

    short_circuit_payload = AgentPipelineInput.model_validate(
        _input(
            candidate_content_item_ids=[],
            configuration={"strategy": "invalid_strategy"},
            input_data={"domain": "   ", "segmentation_tags": "not-a-list"},
        )
    )
    assert pipeline.process(short_circuit_payload) == {"ranked_items": []}
    assert pipeline.last_state is not None
    assert pipeline.last_state.phase == "short_circuited"
    assert pipeline.last_state.strategy == "tags"
    assert pipeline.last_state.domain_counts == {"unknown": 1}
    assert pipeline.last_state.as_dict()["profile_outcomes"][0]["status"] == "short_circuited"

    p_ranked = AgentPipelineInput.model_validate(
        _input(
            input_data={
                "master_profile_id": "55555555-5555-5555-5555-555555555555",
                "domain": "retail",
                "segmentation_tags": ["loyal"],
            }
        )
    )
    p_domain_skip = AgentPipelineInput.model_validate(
        _input(
            input_data={
                "master_profile_id": "66666666-6666-6666-6666-666666666666",
                "domain": "finance",
                "segmentation_tags": ["vip"],
            }
        )
    )
    batch_results = pipeline.process_batch([p_ranked, p_domain_skip])
    assert len(batch_results) == 2
    completed_state = pipeline.last_state
    assert completed_state is not None
    assert completed_state.phase == "completed"
    assert completed_state.profiles_ranked_count == 1
    assert completed_state.profiles_skipped_domain_count == 1
    assert completed_state.total_ranked_items == 1
    assert completed_state.reason_counts == {"segment_tag_overlap": 1}
    assert completed_state.domain_counts == {"finance": 1, "retail": 1}
    completed_dict = completed_state.as_dict()
    assert completed_dict["error"] is None
    assert [o["status"] for o in completed_dict["profile_outcomes"]] == [
        "ranked",
        "skipped_domain_mismatch",
    ]
    assert isinstance(completed_state.profile_outcomes[0], ProfileRankingOutcome)

    fail_connection = FakeConnection(
        [],
        diagnostics={
            "tenant_candidates": 2,
            "active_candidates": 2,
            "domain_candidates": 2,
            "rankable_candidates": 2,
        },
    )
    fail_pipeline = RankingRecommendationPipeline(
        connection_factory=lambda: fail_connection,
        state_observer=observed_states.append,
    )
    with pytest.raises(ValueError, match="minimum_score"):
        fail_pipeline.process(p_ranked)
    assert fail_pipeline.last_state is not None
    assert fail_pipeline.last_state.phase == "failed"
    assert fail_pipeline.last_state.error is not None
    assert "minimum_score" in fail_pipeline.last_state.error
    assert len(observed_states) == 3

