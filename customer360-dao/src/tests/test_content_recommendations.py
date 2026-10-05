import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from redis.exceptions import ConnectionError
from pydantic import ValidationError

import leo_customer360_dao.repositories.content_repository as content_repository
from leo_customer360_dao.repositories.content_repository import ContentRepository
from leo_customer360_dao.schemas.content import ContentItemCreate, ContentItemUpdate

TENANT_ID = UUID("11111111-1111-1111-1111-111111111111")
PROFILE_ID = UUID("22222222-2222-2222-2222-222222222222")
SEGMENT_ID = UUID("33333333-3333-3333-3333-333333333333")
CONTENT_ID = UUID("44444444-4444-4444-4444-444444444444")


class FakeRedis:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def delete(self, key):
        return self.values.pop(key, None) is not None


@pytest.fixture(autouse=True)
def disable_redis(monkeypatch):
    monkeypatch.setattr(content_repository, "get_redis_client", lambda: None)


def test_content_schema_normalizes_missing_tags_and_rejects_null_tags():
    content = ContentItemCreate(
        tenant_id=TENANT_ID,
        item_type="article",
        title="Article",
    )

    assert content.segment_tags == []
    with pytest.raises(ValidationError):
        ContentItemCreate(
            tenant_id=TENANT_ID,
            item_type="article",
            title="Article",
            segment_tags=None,
        )
    with pytest.raises(ValidationError):
        ContentItemUpdate(segment_tags=None)


def _recommendation_row(**overrides):
    now = datetime.now(timezone.utc)
    row = {
        "content_item_id": CONTENT_ID,
        "tenant_id": TENANT_ID,
        "domain": "retail",
        "item_type": "product",
        "title": "Recommended product",
        "summary": "Product summary",
        "image_url": None,
        "cta_label": "View",
        "cta_url": "https://example.com/product",
        "segment_tags": ["loyal"],
        "published_at": now,
        "status_code": 1,
        "created_at": now,
        "updated_at": now,
        "matched_tags": ["loyal"],
        "segment_id": SEGMENT_ID,
        "agent_code": "product_recommendation",
        "rank": 1,
        "score": 0.75,
        "semantic_score": None,
        "tag_score": 0.75,
        "strategy": "tags",
        "reason": "segment_tag_overlap",
        "generated_at": now,
    }
    row.update(overrides)
    return row


def _repository_with_query_results(rows=None):
    session = MagicMock()
    recommendations_result = MagicMock()
    recommendations_result.mappings.return_value.all.return_value = (
        rows if rows is not None else []
    )
    session.execute.return_value = recommendations_result
    session.scalar.return_value = True
    return ContentRepository(session), session


def test_recommendations_combine_current_segment_results_by_default():
    repository, session = _repository_with_query_results()

    assert repository.get_recommended_items(TENANT_ID, PROFILE_ID) == []

    assert session.execute.call_count == 1
    recommendations_query, query_params = session.execute.call_args.args
    assert "tenant_id = :tenant_id AND master_profile_id = :mpid" in str(
        recommendations_query
    )
    assert session.scalar.call_count == 1
    assert "recommendation_items.*" not in str(recommendations_query)
    assert "LEFT JOIN" not in str(recommendations_query)
    assert "cdp_profile_recommendations" in str(recommendations_query)
    assert "cdp_profile_recommendation_runs" in str(recommendations_query)
    assert "DISTINCT ON (content_item_id)" in str(recommendations_query)
    assert "recommendation_run.status = 'SUCCEEDED'" in str(recommendations_query)
    assert "recommendation.semantic_score" in str(recommendations_query)
    assert "recommendation.tag_score" in str(recommendations_query)
    assert "recommendation.strategy" in str(recommendations_query)
    assert "segment.segment_tag = ANY(profile.tags)" in str(recommendations_query)
    assert "CAST(:segment_id AS uuid) IS NULL" in str(recommendations_query)
    assert query_params["segment_id"] is None
    assert query_params["tenant_id"] == str(TENANT_ID)
    assert query_params["mpid"] == str(PROFILE_ID)


def test_recommendations_can_be_filtered_to_one_segment():
    repository, session = _repository_with_query_results()

    repository.get_recommended_items(
        TENANT_ID,
        PROFILE_ID,
        segment_id=SEGMENT_ID,
        item_type="product",
        limit=4,
    )

    query_params = session.execute.call_args.args[1]
    assert query_params == {
        "tenant_id": str(TENANT_ID),
        "mpid": str(PROFILE_ID),
        "segment_id": str(SEGMENT_ID),
        "item_type": "product",
        "limit": 4,
    }


def test_missing_profile_is_rejected_by_the_recommendation_query():
    repository, session = _repository_with_query_results()
    session.scalar.return_value = False

    with pytest.raises(ValueError, match="CdpMasterProfile"):
        repository.get_recommended_items(TENANT_ID, PROFILE_ID)

    assert session.execute.call_count == 1
    existence_query, existence_params = session.scalar.call_args.args
    assert "tenant_id = :tenant_id AND master_profile_id = :mpid" in str(existence_query)
    assert existence_params == {"tenant_id": str(TENANT_ID), "mpid": str(PROFILE_ID)}


@pytest.mark.parametrize(
    ("environment", "should_log_sql"),
    [("development", True), ("dev", True), ("production", False)],
)
def test_recommendation_sql_is_debug_logged_only_in_development(
    environment,
    should_log_sql,
    monkeypatch,
    caplog,
):
    monkeypatch.setattr(content_repository.settings, "environment", environment)
    repository, _session = _repository_with_query_results()

    with caplog.at_level(logging.DEBUG, logger=content_repository.__name__):
        repository.get_recommended_items(TENANT_ID, PROFILE_ID)

    assert ("WITH profile AS" in caplog.text) is should_log_sql


def test_recommendation_results_are_cached_per_tenant_and_profile(monkeypatch):
    redis_client = FakeRedis()
    monkeypatch.setattr(
        content_repository,
        "get_redis_client",
        lambda: redis_client,
    )
    repository, session = _repository_with_query_results([_recommendation_row()])

    first_result = repository.get_recommended_items(TENANT_ID, PROFILE_ID)
    cached_result = repository.get_recommended_items(TENANT_ID, PROFILE_ID)

    assert first_result == cached_result
    assert first_result[0]["content_item_id"] == CONTENT_ID
    assert first_result[0]["generated_at"].tzinfo is not None
    assert session.execute.call_count == 1
    session.scalar.assert_not_called()
    assert any(
        key.startswith(repository._recommendation_generation_key(TENANT_ID) + ":")
        and key.endswith(":limit:8")
        for key in redis_client.values
    )


def test_recommendation_cache_invalidation_rotates_tenant_generation(monkeypatch):
    redis_client = FakeRedis()
    monkeypatch.setattr(
        content_repository,
        "get_redis_client",
        lambda: redis_client,
    )
    repository, session = _repository_with_query_results([_recommendation_row()])
    repository.get_recommended_items(TENANT_ID, PROFILE_ID)
    session.execute.reset_mock()

    repository.invalidate_recommendation_cache(TENANT_ID)
    repository.get_recommended_items(TENANT_ID, PROFILE_ID)

    assert session.execute.call_count == 1
    generation_key = repository._recommendation_generation_key(TENANT_ID)
    first_generation = redis_client.values[generation_key]
    repository.invalidate_recommendation_cache(TENANT_ID)
    assert redis_client.values[generation_key] != first_generation


@pytest.mark.parametrize(
    "overrides",
    [
        {"tenant_id": CONTENT_ID},
        {"master_profile_id": CONTENT_ID},
        {"segment_id": SEGMENT_ID},
        {"item_type": "product"},
        {"limit": 4},
    ],
)
def test_recommendation_cache_keys_isolate_tenants_profiles_and_filters(
    overrides, monkeypatch
):
    redis_client = FakeRedis()
    monkeypatch.setattr(content_repository, "get_redis_client", lambda: redis_client)
    repository, session = _repository_with_query_results()
    repository.get_recommended_items(TENANT_ID, PROFILE_ID)

    params = {"tenant_id": TENANT_ID, "master_profile_id": PROFILE_ID, **overrides}
    repository.get_recommended_items(**params)

    assert session.execute.call_count == 2


def test_invalid_cached_payload_is_logged_and_replaced(monkeypatch, caplog):
    redis_client = FakeRedis()
    monkeypatch.setattr(content_repository, "get_redis_client", lambda: redis_client)
    repository, session = _repository_with_query_results([_recommendation_row()])
    expected = repository.get_recommended_items(TENANT_ID, PROFILE_ID)
    key = next(key for key in redis_client.values if key.endswith(":limit:8"))
    redis_client.values[key] = "invalid-json"

    assert repository.get_recommended_items(TENANT_ID, PROFILE_ID) == expected
    assert session.execute.call_count == 2
    assert "Invalid recommendation cache payload" in caplog.text


def test_redis_failure_falls_back_to_database_with_warning(monkeypatch, caplog):
    redis_client = MagicMock()
    redis_client.get.side_effect = ConnectionError("Redis unavailable")
    monkeypatch.setattr(content_repository, "get_redis_client", lambda: redis_client)
    repository, session = _repository_with_query_results([_recommendation_row()])

    assert len(repository.get_recommended_items(TENANT_ID, PROFILE_ID)) == 1
    assert session.execute.call_count == 1
    assert "Failed to read recommendation cache generation" in caplog.text
