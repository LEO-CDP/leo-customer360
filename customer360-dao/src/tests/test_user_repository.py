"""Unit tests for the shared UserRepository.

HTTP dependency and response tests stay in customer360-api/tests. This file
covers repository CRUD, tenant filters, SSO links, and profile caching.
"""

import json
import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from leo_customer360_dao.models.system import SysUser, SysUserInfo
from leo_customer360_dao.repositories.user_repository import UserRepository
from leo_customer360_dao.schemas.user import UserCreate, UserUpdate


@pytest.fixture
def tenant_id() -> uuid.UUID:
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def user_id() -> uuid.UUID:
    return uuid.UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture
def mock_db() -> Session:
    return MagicMock(spec=Session)


@pytest.fixture
def mock_user(tenant_id: uuid.UUID, user_id: uuid.UUID) -> SysUser:
    user = MagicMock(spec=SysUser)
    user.user_id = user_id
    user.tenant_id = tenant_id
    user.username = "testuser"
    user.email = "test@example.com"
    user.full_name = "Test User"
    user.status = "ACTIVE"
    user.job_title = "Engineer"
    user.department = "Engineering"
    user.organization_id = None
    user.language_code = "en"
    user.timezone = "UTC"
    user.last_login_at = None
    user.created_at = datetime.now()
    user.updated_at = datetime.now()
    user.sso_identities = []
    return user


def _query(mock_db: Session, result):
    query = MagicMock()
    mock_db.query.return_value = query
    query.options.return_value = query
    query.filter.return_value = query
    query.first.return_value = result
    query.offset.return_value = query
    query.limit.return_value = query
    query.all.return_value = result if isinstance(result, list) else []
    return query


def test_get_user_by_id_is_tenant_scoped(mock_db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, mock_user: SysUser):
    _query(mock_db, mock_user)

    result = UserRepository(mock_db).get_user_by_id(user_id, tenant_id)

    assert result is mock_user
    mock_db.query.assert_called_once_with(SysUser)


def test_user_lookup_and_list_apply_filters(mock_db: Session, tenant_id: uuid.UUID, mock_user: SysUser):
    query = _query(mock_db, mock_user)
    repository = UserRepository(mock_db)

    assert repository.get_user_by_username("TestUser", tenant_id) is mock_user
    assert repository.get_user_by_email("TEST@EXAMPLE.COM", tenant_id) is mock_user
    assert repository.list_users(tenant_id, status="ACTIVE", skip=10, limit=20) == []

    assert query.filter.call_count >= 3
    query.offset.assert_called_once_with(10)
    query.limit.assert_called_once_with(20)


def test_create_user_normalizes_identity_fields(mock_db: Session, tenant_id: uuid.UUID):
    mock_db.flush = MagicMock()
    mock_db.refresh = MagicMock()
    payload = UserCreate(username="NewUser", email="NEW@example.com", full_name="New User")

    user = UserRepository(mock_db).create_user(tenant_id, payload)

    assert user.username == "newuser"
    assert user.email == "new@example.com"
    mock_db.add.assert_called_once()
    assert mock_db.flush.call_count == 1


def test_update_and_deactivate_user_flush_changes(mock_db: Session, mock_user: SysUser):
    mock_db.flush = MagicMock()
    mock_db.refresh = MagicMock()
    repository = UserRepository(mock_db)

    with patch("leo_customer360_dao.repositories.user_repository.get_redis_client", return_value=None):
        updated = repository.update_user(mock_user, UserUpdate(full_name="Updated Name"))
        assert updated.full_name == "Updated Name"
        assert repository.deactivate_user(mock_user).status == "INACTIVE"
    assert mock_db.flush.call_count == 2


def test_delete_user_removes_row_and_invalidates_cache(mock_db: Session, mock_user: SysUser):
    mock_db.delete = MagicMock()
    mock_db.flush = MagicMock()

    with patch("leo_customer360_dao.repositories.user_repository.get_redis_client", return_value=None):
        UserRepository(mock_db).delete_user(mock_user)

    mock_db.delete.assert_called_once_with(mock_user)
    mock_db.flush.assert_called_once()


def test_sso_identity_link_and_unlink(mock_db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID):
    mock_db.add = MagicMock()
    mock_db.delete = MagicMock()
    mock_db.flush = MagicMock()
    mock_db.refresh = MagicMock()
    userinfo = MagicMock(spec=SysUserInfo)
    userinfo.user_id = user_id
    userinfo.tenant_id = tenant_id

    repository = UserRepository(mock_db)
    linked = repository.link_sso_identity(user_id, tenant_id, "KEYCLOAK", "subject-1")
    repository.unlink_sso_identity(userinfo)

    assert linked.auth_provider == "KEYCLOAK"
    assert linked.provider_subject_id == "subject-1"
    mock_db.add.assert_called_once()
    mock_db.delete.assert_called_once_with(userinfo)


def test_profile_cache_hit_skips_database(mock_db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID):
    cached = {"user_id": str(user_id), "status": "ACTIVE"}
    redis_client = MagicMock()
    redis_client.get.return_value = json.dumps(cached)

    with patch("leo_customer360_dao.repositories.user_repository.get_redis_client", return_value=redis_client):
        result = UserRepository(mock_db).get_user_by_id_cached(user_id, tenant_id)

    assert result == cached
    mock_db.query.assert_not_called()


def test_profile_cache_miss_reads_and_populates_cache(
    mock_db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, mock_user: SysUser
):
    _query(mock_db, mock_user)
    redis_client = MagicMock()
    redis_client.get.return_value = None

    with (
        patch("leo_customer360_dao.repositories.user_repository.get_redis_client", return_value=redis_client),
        patch("leo_customer360_dao.schemas.user.UserResponse.model_validate") as validate,
    ):
        validate.return_value.model_dump.return_value = {"user_id": str(user_id), "status": "ACTIVE"}
        result = UserRepository(mock_db).get_user_by_id_cached(user_id, tenant_id)

    assert result["status"] == "ACTIVE"
    redis_client.set.assert_called_once()


def test_profile_cache_write_operations_invalidate_cache(mock_db: Session, mock_user: SysUser):
    mock_db.flush = MagicMock()
    mock_db.refresh = MagicMock()
    redis_client = MagicMock()

    with patch("leo_customer360_dao.repositories.user_repository.get_redis_client", return_value=redis_client):
        repository = UserRepository(mock_db)
        repository.update_user(mock_user, UserUpdate(full_name="Changed"))
        repository.deactivate_user(mock_user)

    assert redis_client.delete.call_count == 2
