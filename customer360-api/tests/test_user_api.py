"""HTTP dependency tests for the user API.

UserRepository CRUD, filtering, tenant queries, and caching are tested in the
leo-customer360-dao package. This file covers only FastAPI dependency behavior.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from core.routers.user_api import get_current_user, get_db_session, get_tenant_id


@pytest.fixture
def tenant_id() -> uuid.UUID:
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def user_id() -> uuid.UUID:
    return uuid.UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture
def mock_request(tenant_id: uuid.UUID, user_id: uuid.UUID) -> Request:
    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.state.tenant_id = tenant_id
    request.state.user_id = user_id
    return request


@pytest.fixture
def mock_db_session() -> Session:
    return MagicMock(spec=Session)


def test_get_db_session_sets_identity_and_closes_session(mock_request: Request):
    with patch("core.routers.user_api.SessionLocal") as session_factory:
        session = MagicMock(spec=Session)
        session_factory.return_value = session

        database = get_db_session(mock_request)
        assert next(database) is session
        with pytest.raises(StopIteration):
            next(database)

    session.execute.assert_called_once()
    session.close.assert_called_once()


def test_get_current_user_returns_cached_user(mock_request: Request, mock_db_session: Session):
    cached_user = {"user_id": str(mock_request.state.user_id), "status": "ACTIVE"}

    with patch("core.routers.user_api.UserRepository") as repository_type:
        repository_type.return_value.get_user_by_id_cached.return_value = cached_user
        assert get_current_user(mock_request, mock_db_session) == cached_user


def test_get_current_user_requires_user_id(mock_request: Request, mock_db_session: Session):
    mock_request.state.user_id = None

    with pytest.raises(HTTPException) as error:
        get_current_user(mock_request, mock_db_session)

    assert error.value.status_code == 401


@pytest.mark.parametrize("status", ["INACTIVE", None])
def test_get_current_user_rejects_missing_or_inactive_user(
    mock_request: Request, mock_db_session: Session, status: str | None
):
    cached_user = {"user_id": str(mock_request.state.user_id), "status": status} if status else None

    with patch("core.routers.user_api.UserRepository") as repository_type:
        repository_type.return_value.get_user_by_id_cached.return_value = cached_user
        with pytest.raises(HTTPException) as error:
            get_current_user(mock_request, mock_db_session)

    assert error.value.status_code == 403


def test_get_tenant_id_returns_request_tenant(mock_request: Request, tenant_id: uuid.UUID):
    assert get_tenant_id(mock_request) == tenant_id


def test_get_tenant_id_requires_request_tenant(mock_request: Request):
    mock_request.state.tenant_id = None

    with pytest.raises(HTTPException) as error:
        get_tenant_id(mock_request)

    assert error.value.status_code == 401
