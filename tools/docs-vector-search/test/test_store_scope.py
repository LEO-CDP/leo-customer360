from __future__ import annotations

from src.store import DocumentChunkRepository


class _Cursor:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.log.append((sql, params))

    def fetchall(self):
        return []


class _Conn:
    def __init__(self):
        self.log = []

    def cursor(self):
        return _Cursor(self.log)


def test_searches_are_limited_to_the_path_prefix_when_one_is_set():
    conn = _Conn()
    repo = DocumentChunkRepository(conn, "playbooks/")
    repo.vector_search([0.0] * 384, 5)
    repo.keyword_search("segment", 5)

    for sql, params in conn.log:
        assert "starts_with(" in sql
        assert params.count("playbooks/") == 2


def test_searches_are_unrestricted_without_a_prefix():
    conn = _Conn()
    DocumentChunkRepository(conn).keyword_search("segment", 5)

    _, params = conn.log[0]
    assert params.count(None) == 2
