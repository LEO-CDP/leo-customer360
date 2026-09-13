"""Reusable document-indexing and reindex-job services."""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from . import store
from .config import PG_SCHEMA
from .corpus import Chunk, load_chunks
from .providers import embed


class EmptyCorpusError(RuntimeError):
    """Raised when indexing would otherwise risk pruning a valid existing index."""


@dataclass(frozen=True)
class IndexResult:
    total_chunks: int
    unchanged_chunks: int
    embedded_chunks: int
    pruned_chunks: int
    indexed_chunks: int
    dry_run: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class IndexingService:
    """Build the document index through injected corpus, embedding, and storage seams."""

    def __init__(
        self,
        chunk_loader: Callable[[], list[Chunk]] = load_chunks,
        connection_factory: Callable = store.connect,
        repository_factory: Callable = store.DocumentChunkRepository,
        embedder: Callable = embed,
    ) -> None:
        self._chunk_loader = chunk_loader
        self._connection_factory = connection_factory
        self._repository_factory = repository_factory
        self._embedder = embedder

    def rebuild(self, *, dry_run: bool = False, batch: int = 64) -> IndexResult:
        if batch < 1:
            raise ValueError("batch must be positive")
        chunks = self._chunk_loader()
        if not chunks:
            raise EmptyCorpusError(
                "Refusing to build: no .md chunks found under CORPUS_DIR — check that "
                "the corpus is mounted and non-empty. The existing index was left untouched."
            )

        with self._connection_factory() as connection:
            repository = self._repository_factory(connection)
            repository.init_schema()
            existing = repository.existing_hashes()
            fresh = [chunk for chunk in chunks if existing.get(chunk.id) != chunk.content_hash]
            if dry_run:
                return IndexResult(
                    total_chunks=len(chunks),
                    unchanged_chunks=len(chunks) - len(fresh),
                    embedded_chunks=0,
                    pruned_chunks=0,
                    indexed_chunks=repository.count(),
                    dry_run=True,
                )

            embedded = 0
            for offset in range(0, len(fresh), batch):
                part = fresh[offset : offset + batch]
                vectors = self._embedder([chunk.text for chunk in part], task="document")
                repository.upsert(
                    [
                        (
                            chunk.id,
                            chunk.path,
                            chunk.title,
                            chunk.heading,
                            chunk.ordinal,
                            chunk.content_hash,
                            chunk.text,
                            vector,
                        )
                        for chunk, vector in zip(part, vectors)
                    ]
                )
                embedded += len(part)

            pruned = repository.prune([chunk.id for chunk in chunks])
            return IndexResult(
                total_chunks=len(chunks),
                unchanged_chunks=len(chunks) - len(fresh),
                embedded_chunks=embedded,
                pruned_chunks=pruned,
                indexed_chunks=repository.count(),
            )


def build(*, dry_run: bool = False, batch: int = 64) -> IndexResult:
    """Compatibility entry point used by the CLI and existing deployment scripts."""
    result = IndexingService().rebuild(dry_run=dry_run, batch=batch)
    print(
        f"{result.total_chunks} chunks — {result.unchanged_chunks} unchanged, "
        f"{result.embedded_chunks} embedded"
    )
    if not result.dry_run:
        print(
            f"Done — {result.indexed_chunks} chunks in {PG_SCHEMA}.doc_chunks "
            f"(pruned {result.pruned_chunks})."
        )
    return result


@dataclass
class ReindexJob:
    id: str
    status: str = "queued"
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    result: IndexResult | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["result"] = self.result.as_dict() if self.result else None
        return payload


class ReindexInProgressError(RuntimeError):
    """Raised when a second reindex request arrives before the first one finishes."""

    def __init__(self, job_id: str) -> None:
        super().__init__(f"Reindex job {job_id} is already running")
        self.job_id = job_id


class ReindexJobManager:
    """Run at most one reindex task per API process and expose its status."""

    def __init__(
        self,
        indexer: IndexingService,
        on_completed: Callable[[IndexResult], None] | None = None,
        max_history: int = 20,
    ) -> None:
        self._indexer = indexer
        self._on_completed = on_completed
        self._max_history = max(1, max_history)
        self._jobs: dict[str, ReindexJob] = {}
        self._active_job_id: str | None = None

    async def start(self) -> ReindexJob:
        if self._active_job_id:
            active = self._jobs[self._active_job_id]
            if active.status in {"queued", "running"}:
                raise ReindexInProgressError(active.id)

        job = ReindexJob(id=uuid.uuid4().hex, created_at=self._timestamp())
        self._jobs[job.id] = job
        self._active_job_id = job.id
        asyncio.create_task(self._run(job))
        self._trim_history()
        return job

    def get(self, job_id: str) -> ReindexJob | None:
        return self._jobs.get(job_id)

    async def _run(self, job: ReindexJob) -> None:
        job.status = "running"
        job.started_at = self._timestamp()
        try:
            job.result = await asyncio.to_thread(self._indexer.rebuild)
            job.status = "completed"
            if self._on_completed:
                self._on_completed(job.result)
        except Exception as exc:  # noqa: BLE001 - status must capture all job failures
            job.status = "failed"
            job.error = str(exc)
        finally:
            job.finished_at = self._timestamp()
            if self._active_job_id == job.id:
                self._active_job_id = None

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _trim_history(self) -> None:
        finished = [
            job_id
            for job_id, job in self._jobs.items()
            if job.status in {"completed", "failed"}
        ]
        for job_id in finished[: max(0, len(self._jobs) - self._max_history)]:
            self._jobs.pop(job_id, None)