"""Unit tests for identity CRUD query filtering and pagination."""

from leo_customer360_dao.crud.identity import list_master_profiles_page


class _Rows:
    def all(self):
        return []

    def scalar_one(self):
        return 0


class _Session:
    def execute(self, statement, params=None):
        return _Rows()


def test_master_profiles_page_supports_days_filter():
    result = list_master_profiles_page(_Session(), days=30, page=1, page_size=25)

    assert result["items"] == []
    assert result["pagination"]["total"] == 0
    assert result["pagination"]["total_pages"] == 1