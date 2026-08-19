from core.repositories.reporting_respository import ReportingRepository


def test_reporting_repository_exposes_reporting_queries():
    repo = ReportingRepository(session=None)

    assert callable(repo.count_raw_profiles)
    assert callable(repo.count_master_profiles)
    assert callable(repo.raw_profiles_by_status)
    assert callable(repo.raw_profiles_by_domain)
    assert callable(repo.master_profiles_by_domain)
    assert callable(repo.raw_profiles_by_source_system)
    assert callable(repo.count_duplicate_master_profiles)
    assert callable(repo.list_duplicate_master_profiles)
    assert callable(repo.identity_graph_coverage)
    assert callable(repo.persona_analytics_summary)
