"""Small database fakes shared by DAO repository tests."""

from typing import Any, Optional


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