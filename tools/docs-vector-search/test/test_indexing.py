from __future__ import annotations

import asyncio

from src.corpus import Chunk
from src.indexing import (
    EmptyCorpusError,
    IndexResult,
    IndexingService,
    ReindexInProgressError,
    ReindexJobManager,
)


def _chunk(chunk_id: str) -> Chunk:
    return Chunk(
        id=chunk_id,
        path="guide.md",
        title="Guide",
        heading="Setup",
        ordinal=0,
        text=f"text for {chunk_id}",
        content_hash=f"hash-{chunk_id}",
    )


class _Connection:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _Repository:
    def __init__(self, connection):
        self.upserted = []

    def init_schema(self):
        pass

    def existing_hashes(self):
        return {}

    def upsert(self, rows):
        self.upserted.extend(rows)

    def prune(self, keep_ids):
        return 1

    def count(self):
        return 2


def test_indexing_service_reports_changes_and_uses_injected_dependencies():
    repository = None

    def repository_factory(connection):
        nonlocal repository
        repository = _Repository(connection)
        return repository

    service = IndexingService(
        chunk_loader=lambda: [_chunk("one"), _chunk("two")],
        connection_factory=_Connection,
        repository_factory=repository_factory,
        embedder=lambda texts, task: [[float(index)] for index, _ in enumerate(texts, start=1)],
    )

    result = service.rebuild(batch=1)

    assert result == IndexResult(2, 0, 2, 1, 2)
    assert len(repository.upserted) == 2


def test_indexing_service_refuses_empty_corpus_before_opening_database():
    service = IndexingService(
        chunk_loader=lambda: [],
        connection_factory=lambda: (_ for _ in ()).throw(AssertionError("database opened")),
    )

    try:
        service.rebuild()
    except EmptyCorpusError as exc:
        assert ".md" in str(exc)
    else:
        raise AssertionError("empty corpus was accepted")


def test_reindex_job_manager_rejects_duplicate_active_jobs():
    async def run():
        class FakeIndexer:
            def rebuild(self):
                return IndexResult(1, 1, 0, 0, 1)

        manager = ReindexJobManager(FakeIndexer())
        first = await manager.start()
        try:
            await manager.start()
        except ReindexInProgressError as exc:
            assert exc.job_id == first.id
        else:
            raise AssertionError("duplicate reindex was accepted")

        for _ in range(20):
            await asyncio.sleep(0)
            if manager.get(first.id).status == "completed":
                break
        assert manager.get(first.id).as_dict()["result"]["indexed_chunks"] == 1

    asyncio.run(run())