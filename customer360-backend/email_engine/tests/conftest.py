"""Pytest fixtures for email_engine unit tests. All DB access is mocked, so no
real PostgreSQL is required (mirrors identity_resolution/tests/conftest.py)."""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_cursor():
    return MagicMock()


@pytest.fixture
def mock_conn(mock_cursor):
    conn = MagicMock()
    cursor_ctx = MagicMock()
    cursor_ctx.__enter__.return_value = mock_cursor
    cursor_ctx.__exit__.return_value = False
    conn.cursor.return_value = cursor_ctx
    return conn
