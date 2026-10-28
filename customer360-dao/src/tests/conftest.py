"""Shared database fakes and cursor fixtures for DAO tests."""

from unittest.mock import MagicMock
from typing import Any, Optional

import pytest


class FakeQueryResult:
    """Minimal result double for mapping-based repository queries."""

    def __init__(self, row: Optional[dict[str, Any]]):
        self._row = row

    def mappings(self) -> "FakeQueryResult":
        return self

    def first(self) -> Optional[dict[str, Any]]:
        return self._row


class FakeDBSession:
    """Scripted session double that records SQL and bound parameters."""

    def __init__(self, script: Optional[list[Any]] = None):
        self.script = list(script or [])
        self.executed: list[tuple[str, Optional[dict[str, Any]]]] = []

    def execute(self, statement: Any, params: Optional[dict[str, Any]] = None) -> Any:
        self.executed.append((str(statement), params))
        if self.script:
            return self.script.pop(0)
        return FakeQueryResult(None)


@pytest.fixture
def mock_cursor() -> MagicMock:
    """A MagicMock standing in for a psycopg2 cursor."""
    return MagicMock()


@pytest.fixture
def mock_conn(mock_cursor: MagicMock) -> MagicMock:
    """A MagicMock connection whose cursor context yields ``mock_cursor``."""
    conn = MagicMock()
    cursor_context = MagicMock()
    cursor_context.__enter__.return_value = mock_cursor
    cursor_context.__exit__.return_value = False
    conn.cursor.return_value = cursor_context
    return conn