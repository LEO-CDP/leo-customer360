from unittest.mock import MagicMock
from uuid import UUID

from leo_customer360_dao.repositories.content_repository import ContentRepository

TENANT_ID = UUID("11111111-1111-1111-1111-111111111111")
PROFILE_ID = UUID("22222222-2222-2222-2222-222222222222")
SEGMENT_ID = UUID("33333333-3333-3333-3333-333333333333")


def _repository_with_query_results():
    session = MagicMock()
    profile_result = MagicMock()
    profile_result.mappings.return_value.first.return_value = {"domain": "retail"}
    recommendations_result = MagicMock()
    recommendations_result.mappings.return_value.all.return_value = []
    session.execute.side_effect = [profile_result, recommendations_result]
    return ContentRepository(session), session


def test_recommendations_combine_current_segment_results_by_default():
    repository, session = _repository_with_query_results()

    assert repository.get_recommended_items(TENANT_ID, PROFILE_ID) == []

    profile_query, profile_params = session.execute.call_args_list[0].args
    recommendations_query, query_params = session.execute.call_args_list[1].args
    assert "tenant_id = :tenant_id AND master_profile_id = :mpid" in str(profile_query)
    assert profile_params == {
        "tenant_id": str(TENANT_ID),
        "mpid": str(PROFILE_ID),
    }
    assert "cdp_profile_recommendations" in str(recommendations_query)
    assert "cdp_profile_recommendation_runs" in str(recommendations_query)
    assert "DISTINCT ON (content_item_id)" in str(recommendations_query)
    assert "recommendation_run.status = 'SUCCEEDED'" in str(recommendations_query)
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

    query_params = session.execute.call_args_list[1].args[1]
    assert query_params == {
        "tenant_id": str(TENANT_ID),
        "mpid": str(PROFILE_ID),
        "segment_id": str(SEGMENT_ID),
        "item_type": "product",
        "limit": 4,
    }
