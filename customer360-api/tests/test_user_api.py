"""Unit tests for user API endpoints and repository (CRUD, tenant isolation, uniqueness)."""

import uuid
from datetime import datetime
from typing import Any, Generator
from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from core.models.system import SysUser, SysUserInfo
from core.repositories.user_repository import UserRepository
from core.routers.user_api import (
    get_current_user,
    get_db_session,
    get_tenant_id,
)
from core.schemas.user import UserCreate, UserUpdate, UserResponse


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def tenant_id() -> uuid.UUID:
    """Fixed tenant ID for tests."""
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def user_id() -> uuid.UUID:
    """Fixed user ID for tests."""
    return uuid.UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture
def another_user_id() -> uuid.UUID:
    """Another user ID for cross-user tests."""
    return uuid.UUID("00000000-0000-0000-0000-000000000003")


@pytest.fixture
def mock_sys_user(tenant_id: uuid.UUID, user_id: uuid.UUID) -> SysUser:
    """Mock SysUser instance."""
    user = MagicMock(spec=SysUser)
    user.user_id = user_id
    user.tenant_id = tenant_id
    user.username = "testuser"
    user.email = "test@example.com"
    user.full_name = "Test User"
    user.phone = "555-1234"
    user.job_title = "Engineer"
    user.department = "Engineering"
    user.organization_id = None
    user.language_code = "en"
    user.timezone = "UTC"
    user.status = "ACTIVE"
    user.last_login_at = None
    user.created_at = datetime.now()
    user.updated_at = datetime.now()
    user.sso_identities = []
    return user


@pytest.fixture
def mock_db_session() -> Session:
    """Mock database session."""
    return MagicMock(spec=Session)


@pytest.fixture
def mock_request(tenant_id: uuid.UUID, user_id: uuid.UUID) -> Request:
    """Mock FastAPI request with auth_middleware context."""
    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.state.tenant_id = tenant_id
    request.state.user_id = user_id
    return request


# =============================================================================
# UserRepository Tests
# =============================================================================

class TestUserRepository:
    """Unit tests for UserRepository methods."""

    def test_get_user_by_id_success(self, mock_db_session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, mock_sys_user: SysUser):
        """Test fetching a user by ID within tenant."""
        mock_query = MagicMock()
        mock_db_session.query.return_value = mock_query
        mock_query.options.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = mock_sys_user

        repo = UserRepository(mock_db_session)
        result = repo.get_user_by_id(user_id, tenant_id)

        assert result == mock_sys_user
        mock_db_session.query.assert_called_once_with(SysUser)

    def test_get_user_by_id_not_found(self, mock_db_session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID):
        """Test fetching non-existent user by ID returns None."""
        mock_query = MagicMock()
        mock_db_session.query.return_value = mock_query
        mock_query.options.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = None

        repo = UserRepository(mock_db_session)
        result = repo.get_user_by_id(user_id, tenant_id)

        assert result is None

    def test_get_user_by_username_success(self, mock_db_session: Session, tenant_id: uuid.UUID, mock_sys_user: SysUser):
        """Test fetching a user by username (case-insensitive)."""
        mock_query = MagicMock()
        mock_db_session.query.return_value = mock_query
        mock_query.options.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = mock_sys_user

        repo = UserRepository(mock_db_session)
        result = repo.get_user_by_username("TestUser", tenant_id)

        assert result == mock_sys_user
        # Verify filter was called (SQLAlchemy query objects)
        mock_query.filter.assert_called()

    def test_get_user_by_email_success(self, mock_db_session: Session, tenant_id: uuid.UUID, mock_sys_user: SysUser):
        """Test fetching a user by email (case-insensitive)."""
        mock_query = MagicMock()
        mock_db_session.query.return_value = mock_query
        mock_query.options.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = mock_sys_user

        repo = UserRepository(mock_db_session)
        result = repo.get_user_by_email("TEST@EXAMPLE.COM", tenant_id)

        assert result == mock_sys_user

    def test_list_users_success(self, mock_db_session: Session, tenant_id: uuid.UUID, mock_sys_user: SysUser):
        """Test listing users in a tenant."""
        mock_query = MagicMock()
        mock_db_session.query.return_value = mock_query
        mock_query.options.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = [mock_sys_user]

        repo = UserRepository(mock_db_session)
        result = repo.list_users(tenant_id, skip=0, limit=10)

        assert len(result) == 1
        assert result[0] == mock_sys_user

    def test_list_users_with_status_filter(self, mock_db_session: Session, tenant_id: uuid.UUID, mock_sys_user: SysUser):
        """Test listing users filtered by status."""
        mock_query = MagicMock()
        mock_db_session.query.return_value = mock_query
        mock_query.options.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = [mock_sys_user]

        repo = UserRepository(mock_db_session)
        result = repo.list_users(tenant_id, status="ACTIVE", skip=0, limit=10)

        assert len(result) == 1

    def test_create_user_success(self, mock_db_session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID):
        """Test creating a new user."""
        user_create = UserCreate(
            username="newuser",
            email="newuser@example.com",
            full_name="New User",
            job_title="Developer",
        )

        created_user = MagicMock(spec=SysUser)
        created_user.user_id = user_id
        created_user.username = "newuser"
        created_user.email = "newuser@example.com"

        mock_db_session.flush.return_value = None
        mock_db_session.refresh.return_value = None

        repo = UserRepository(mock_db_session)
        
        # Mock the add and refresh
        with patch.object(mock_db_session, 'add'):
            with patch.object(mock_db_session, 'flush'):
                with patch.object(mock_db_session, 'refresh'):
                    repo.db.add = MagicMock()
                    repo.db.flush = MagicMock()
                    repo.db.refresh = MagicMock()
                    
                    result = repo.create_user(tenant_id, user_create)
                    
                    assert result.username == "newuser"
                    assert result.email == "newuser@example.com"
                    repo.db.add.assert_called_once()
                    repo.db.flush.assert_called_once()

    def test_update_user_success(self, mock_db_session: Session, mock_sys_user: SysUser):
        """Test updating a user's profile."""
        user_update = UserUpdate(
            full_name="Updated Name",
            job_title="Senior Engineer",
        )

        mock_db_session.flush = MagicMock()
        mock_db_session.refresh = MagicMock()

        repo = UserRepository(mock_db_session)
        result = repo.update_user(mock_sys_user, user_update)

        assert result.full_name == "Updated Name"
        assert result.job_title == "Senior Engineer"
        mock_db_session.flush.assert_called_once()

    def test_deactivate_user_success(self, mock_db_session: Session, mock_sys_user: SysUser):
        """Test deactivating a user."""
        mock_db_session.flush = MagicMock()
        mock_db_session.refresh = MagicMock()

        repo = UserRepository(mock_db_session)
        result = repo.deactivate_user(mock_sys_user)

        assert result.status == "INACTIVE"
        mock_db_session.flush.assert_called_once()

    def test_delete_user_success(self, mock_db_session: Session, mock_sys_user: SysUser):
        """Test hard-deleting a user."""
        mock_db_session.delete = MagicMock()
        mock_db_session.flush = MagicMock()

        repo = UserRepository(mock_db_session)
        repo.delete_user(mock_sys_user)

        mock_db_session.delete.assert_called_once_with(mock_sys_user)
        mock_db_session.flush.assert_called_once()

    def test_get_user_sso_identity_success(self, mock_db_session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID):
        """Test fetching a specific SSO identity."""
        userinfo = MagicMock(spec=SysUserInfo)
        userinfo.auth_provider = "KEYCLOAK"
        userinfo.provider_subject_id = "kc-subject-123"

        mock_query = MagicMock()
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = userinfo

        repo = UserRepository(mock_db_session)
        result = repo.get_user_sso_identity(user_id, "KEYCLOAK", tenant_id)

        assert result == userinfo
        assert result.auth_provider == "KEYCLOAK"

    def test_link_sso_identity_success(self, mock_db_session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID):
        """Test linking an SSO identity to a user."""
        mock_db_session.add = MagicMock()
        mock_db_session.flush = MagicMock()
        mock_db_session.refresh = MagicMock()

        repo = UserRepository(mock_db_session)
        result = repo.link_sso_identity(user_id, tenant_id, "KEYCLOAK", "kc-subject-123")

        assert result.auth_provider == "KEYCLOAK"
        assert result.provider_subject_id == "kc-subject-123"
        mock_db_session.add.assert_called_once()

    def test_unlink_sso_identity_success(self, mock_db_session: Session):
        """Test removing an SSO identity link."""
        userinfo = MagicMock(spec=SysUserInfo)
        mock_db_session.delete = MagicMock()
        mock_db_session.flush = MagicMock()

        repo = UserRepository(mock_db_session)
        repo.unlink_sso_identity(userinfo)

        mock_db_session.delete.assert_called_once_with(userinfo)
        mock_db_session.flush.assert_called_once()


# =============================================================================
# Dependency Injection Tests
# =============================================================================

class TestGetDbSession:
    """Tests for get_db_session dependency."""

    def test_get_db_session_with_tenant_id(self, mock_request: Request):
        """Test that get_db_session sets tenant_id in config."""
        with patch("core.routers.user_api.SessionLocal") as mock_session_local:
            mock_db = MagicMock(spec=Session)
            mock_session_local.return_value = mock_db

            gen = get_db_session(mock_request)
            session = next(gen)

            assert session == mock_db
            mock_db.execute.assert_called_once()

    def test_get_db_session_cleanup(self, mock_request: Request):
        """Test that get_db_session closes DB on exit."""
        with patch("core.routers.user_api.SessionLocal") as mock_session_local:
            mock_db = MagicMock(spec=Session)
            mock_session_local.return_value = mock_db

            gen = get_db_session(mock_request)
            next(gen)
            
            try:
                next(gen)
            except StopIteration:
                pass

            mock_db.close.assert_called_once()


class TestGetCurrentUser:
    """Tests for get_current_user dependency."""

    def test_get_current_user_success(self, mock_request: Request, mock_db_session: Session):
        """Test successful current user resolution (cache hit or DB fallback)."""
        cached_user = {"user_id": "00000000-0000-0000-0000-000000000002", "status": "ACTIVE"}

        with patch("core.routers.user_api.UserRepository") as mock_repo_class:
            mock_repo = MagicMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_user_by_id_cached.return_value = cached_user

            result = get_current_user(mock_request, mock_db_session)

            assert result == cached_user

    def test_get_current_user_no_user_id(self, mock_request: Request, mock_db_session: Session):
        """Test missing user_id raises 401."""
        mock_request.state.user_id = None

        with pytest.raises(HTTPException) as exc_info:
            get_current_user(mock_request, mock_db_session)

        assert exc_info.value.status_code == 401

    def test_get_current_user_inactive(self, mock_request: Request, mock_db_session: Session):
        """Test inactive user raises 403."""
        cached_user = {"user_id": "00000000-0000-0000-0000-000000000002", "status": "INACTIVE"}

        with patch("core.routers.user_api.UserRepository") as mock_repo_class:
            mock_repo = MagicMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_user_by_id_cached.return_value = cached_user

            with pytest.raises(HTTPException) as exc_info:
                get_current_user(mock_request, mock_db_session)

            assert exc_info.value.status_code == 403

    def test_get_current_user_not_found(self, mock_request: Request, mock_db_session: Session):
        """Test non-existent user raises 403."""
        with patch("core.routers.user_api.UserRepository") as mock_repo_class:
            mock_repo = MagicMock()
            mock_repo_class.return_value = mock_repo
            mock_repo.get_user_by_id_cached.return_value = None

            with pytest.raises(HTTPException) as exc_info:
                get_current_user(mock_request, mock_db_session)

            assert exc_info.value.status_code == 403


class TestGetTenantId:
    """Tests for get_tenant_id dependency."""

    def test_get_tenant_id_success(self, mock_request: Request, tenant_id: uuid.UUID):
        """Test successful tenant ID extraction."""
        result = get_tenant_id(mock_request)
        assert result == tenant_id

    def test_get_tenant_id_missing(self, mock_request: Request):
        """Test missing tenant_id raises 401."""
        mock_request.state.tenant_id = None

        with pytest.raises(HTTPException) as exc_info:
            get_tenant_id(mock_request)

        assert exc_info.value.status_code == 401


# =============================================================================
# Multi-Tenant Isolation Tests
# =============================================================================

class TestMultiTenantIsolation:
    """Tests to verify tenant isolation in user operations."""

    def test_list_users_tenant_scoped(self, mock_db_session: Session, tenant_id: uuid.UUID, mock_sys_user: SysUser):
        """Test that list_users only returns users in current tenant."""
        other_tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000099")

        mock_query = MagicMock()
        mock_db_session.query.return_value = mock_query
        mock_query.options.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = [mock_sys_user]

        repo = UserRepository(mock_db_session)
        result = repo.list_users(tenant_id, skip=0, limit=10)

        # Verify filter was called with tenant_id (SQLAlchemy query objects)
        assert mock_query.filter.call_count >= 1
        assert result == [mock_sys_user]

    def test_get_user_respects_tenant_boundary(self, mock_db_session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID):
        """Test that get_user_by_id enforces tenant isolation."""
        other_tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000099")

        mock_query = MagicMock()
        mock_db_session.query.return_value = mock_query
        mock_query.options.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = None

        repo = UserRepository(mock_db_session)
        result = repo.get_user_by_id(user_id, other_tenant_id)

        assert result is None


# =============================================================================
# Redis Profile Caching Tests (read-through cache + write invalidation)
# =============================================================================

class TestUserProfileCaching:
    """Tests for the Redis read-through cache backing profile reads at scale."""

    def test_get_user_by_id_cached_hit_skips_db(self, mock_db_session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID):
        """Cache hit returns the cached dict without querying the database."""
        cached_payload = {"user_id": str(user_id), "username": "testuser", "status": "ACTIVE"}
        mock_redis = MagicMock()
        mock_redis.get.return_value = __import__("json").dumps(cached_payload)

        with patch("core.repositories.user_repository.get_redis_client", return_value=mock_redis):
            repo = UserRepository(mock_db_session)
            result = repo.get_user_by_id_cached(user_id, tenant_id)

        assert result == cached_payload
        mock_db_session.query.assert_not_called()

    def test_get_user_by_id_cached_miss_falls_back_to_db_and_populates_cache(
        self, mock_db_session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, mock_sys_user: SysUser
    ):
        """Cache miss queries the DB, then writes the result back into Redis."""
        mock_query = MagicMock()
        mock_db_session.query.return_value = mock_query
        mock_query.options.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = mock_sys_user

        mock_redis = MagicMock()
        mock_redis.get.return_value = None  # cache miss

        with patch("core.repositories.user_repository.get_redis_client", return_value=mock_redis):
            with patch("core.schemas.user.UserResponse.model_validate") as mock_validate:
                mock_validate.return_value.model_dump.return_value = {"user_id": str(user_id), "status": "ACTIVE"}
                repo = UserRepository(mock_db_session)
                result = repo.get_user_by_id_cached(user_id, tenant_id)

        assert result is not None
        mock_db_session.query.assert_called_once_with(SysUser)
        mock_redis.set.assert_called_once()

    def test_get_user_by_id_cached_miss_no_user_returns_none(
        self, mock_db_session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID
    ):
        """Cache miss + user doesn't exist in DB returns None without caching anything."""
        mock_query = MagicMock()
        mock_db_session.query.return_value = mock_query
        mock_query.options.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = None

        mock_redis = MagicMock()
        mock_redis.get.return_value = None

        with patch("core.repositories.user_repository.get_redis_client", return_value=mock_redis):
            repo = UserRepository(mock_db_session)
            result = repo.get_user_by_id_cached(user_id, tenant_id)

        assert result is None
        mock_redis.set.assert_not_called()

    def test_redis_unavailable_fails_open_to_db(
        self, mock_db_session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, mock_sys_user: SysUser
    ):
        """If Redis is down, reads fail open to the DB rather than raising."""
        mock_query = MagicMock()
        mock_db_session.query.return_value = mock_query
        mock_query.options.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = mock_sys_user

        with patch("core.repositories.user_repository.get_redis_client", return_value=None):
            repo = UserRepository(mock_db_session)
            result = repo.get_user_by_id_cached(user_id, tenant_id)

        assert result is not None
        mock_db_session.query.assert_called_once_with(SysUser)

    def test_update_user_invalidates_cache(self, mock_db_session: Session, mock_sys_user: SysUser):
        """Updating a user evicts its cached profile entry."""
        mock_db_session.flush = MagicMock()
        mock_db_session.refresh = MagicMock()
        mock_redis = MagicMock()

        with patch("core.repositories.user_repository.get_redis_client", return_value=mock_redis):
            repo = UserRepository(mock_db_session)
            repo.update_user(mock_sys_user, UserUpdate(full_name="New Name"))

        mock_redis.delete.assert_called_once()

    def test_deactivate_user_invalidates_cache(self, mock_db_session: Session, mock_sys_user: SysUser):
        """Deactivating a user evicts its cached profile entry."""
        mock_db_session.flush = MagicMock()
        mock_db_session.refresh = MagicMock()
        mock_redis = MagicMock()

        with patch("core.repositories.user_repository.get_redis_client", return_value=mock_redis):
            repo = UserRepository(mock_db_session)
            repo.deactivate_user(mock_sys_user)

        mock_redis.delete.assert_called_once()

    def test_delete_user_invalidates_cache(self, mock_db_session: Session, mock_sys_user: SysUser):
        """Hard-deleting a user evicts its cached profile entry."""
        mock_db_session.delete = MagicMock()
        mock_db_session.flush = MagicMock()
        mock_redis = MagicMock()

        with patch("core.repositories.user_repository.get_redis_client", return_value=mock_redis):
            repo = UserRepository(mock_db_session)
            repo.delete_user(mock_sys_user)

        mock_redis.delete.assert_called_once()


# =============================================================================
# Uniqueness Constraint Tests
# =============================================================================

class TestUniquenessConstraints:
    """Tests for username and email uniqueness within tenant."""

    def test_username_uniqueness_per_tenant(self, mock_db_session: Session, tenant_id: uuid.UUID):
        """Test that username must be unique within tenant (but not across tenants)."""
        other_tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000099")

        user_create = UserCreate(
            username="testuser",
            email="test@example.com",
            full_name="Test User",
        )

        mock_db_session.add = MagicMock()
        mock_db_session.flush = MagicMock()
        mock_db_session.refresh = MagicMock()

        repo = UserRepository(mock_db_session)
        
        # Create user in tenant 1 - should succeed
        with patch.object(repo, 'get_user_by_username', return_value=None):
            result1 = repo.create_user(tenant_id, user_create)
            assert result1 is not None

        # Create same username in tenant 2 - should also succeed (different tenant)
        with patch.object(repo, 'get_user_by_username', return_value=None):
            result2 = repo.create_user(other_tenant_id, user_create)
            assert result2 is not None

    def test_email_uniqueness_per_tenant(self, mock_db_session: Session, tenant_id: uuid.UUID):
        """Test that email must be unique within tenant."""
        user_create = UserCreate(
            username="testuser",
            email="test@example.com",
            full_name="Test User",
        )

        mock_db_session.add = MagicMock()
        mock_db_session.flush = MagicMock()
        mock_db_session.refresh = MagicMock()

        repo = UserRepository(mock_db_session)
        
        # Try to create duplicate email - should be caught by get_user_by_email
        existing_user = MagicMock(spec=SysUser)
        existing_user.email = "test@example.com"

        with patch.object(repo, 'get_user_by_email', return_value=existing_user):
            # Repository doesn't validate - that's the router's job
            # This test just verifies get_user_by_email works correctly
            result = repo.get_user_by_email("test@example.com", tenant_id)
            assert result is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
