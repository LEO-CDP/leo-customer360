"""FastAPI service — /ask, /search, /reindex, /health. Models load once at startup; a DB
connection is opened per request (safe under the threadpool; low concurrency on 1 vCPU).

    uvicorn src.server:app --port 8001
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import store
from .agent import query, retrieve
from .indexing import (
    IndexingService,
    ReindexInProgressError,
    ReindexJobManager,
)
from .config import (
    CORS_ORIGINS,
    EMBED_PROVIDER,
    EMBED_MODEL,
    LOCAL_LLM_MODEL_PATH,
    DOCS_RERANK_ENABLED,
    DOCS_RERANK_MODEL,
    DOCS_RERANK_PROVIDER,
    GEMINI_EMBEDDING_MODEL,
    GEMINI_LLM_MODEL,
    HYBRID_SEARCH_ENABLED,
    INTERNAL_API_SECRET,
    LLM_PROVIDER,
    OPENAI_LLM_MODEL,
    OPENAI_EMBEDDING_MODEL,
    OPENAI_RERANK_MODEL,
    QUESTION_MAX_LEN,
    RERANK_TOP_K,
    RETRIEVE_TOP_N,
    TOP_K_MAX,
    TOP_N_MAX,
)
from .providers import embed, rerank
from .rate_limit import limiter

_log = logging.getLogger("uvicorn.error")


def _retrieve_sync(question: str, top_n: int) -> list[dict]:
    with store.connect() as conn:
        return retrieve(question, conn, top_n)


def _query_sync(question: str, top_n: int, top_k: int) -> dict:
    with store.connect() as conn:
        return query(question, conn, top_n, top_k)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await limiter.ping()
    # Warm the models so the first request isn't slow, and fail fast if a model
    # or the DB is misconfigured.
    embed(["warmup"], task="query")
    if DOCS_RERANK_ENABLED and DOCS_RERANK_PROVIDER == "local":
        rerank("warmup", ["warmup"])
    with store.connect() as conn:
        app.state.doc_count = store.count(conn)
    try:
        yield
    finally:
        await limiter.close()


app = FastAPI(title="LEO Customer 360 — Document Vector Search", lifespan=lifespan)


def _update_loaded_chunk_count(result) -> None:
    app.state.doc_count = result.indexed_chunks


reindex_jobs = ReindexJobManager(IndexingService(), on_completed=_update_loaded_chunk_count)

# Browser access from the static docs site (cross-origin). Exact origins only; no
# credentials, so we stay off the wildcard-with-credentials trap.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type"],
    allow_credentials=False,
    max_age=600,
)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=QUESTION_MAX_LEN)
    top_n: int = Field(RETRIEVE_TOP_N, ge=1, le=TOP_N_MAX)
    top_k: int = Field(RERANK_TOP_K, ge=1, le=TOP_K_MAX)


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=QUESTION_MAX_LEN)
    top_n: int = Field(RETRIEVE_TOP_N, ge=1, le=TOP_N_MAX)


def _require_reindex_access(request: Request) -> None:
    if not INTERNAL_API_SECRET or not limiter.is_internal(request):
        raise HTTPException(
            status_code=403,
            detail="Reindex requires the configured X-Internal-Auth credential.",
        )


@app.get("/health")
def health():
    embed_model = {
        "openai": OPENAI_EMBEDDING_MODEL,
        "gemini": GEMINI_EMBEDDING_MODEL,
        "local": EMBED_MODEL,
    }.get(EMBED_PROVIDER, EMBED_MODEL)
    generator = {
        "openai": OPENAI_LLM_MODEL,
        "gemini": GEMINI_LLM_MODEL,
        "local": LOCAL_LLM_MODEL_PATH.rsplit("/", 1)[-1],
    }.get(LLM_PROVIDER, LLM_PROVIDER)
    return {
        "status": "ok",
        "loaded_chunks": getattr(app.state, "doc_count", None),
        "embedding_provider": EMBED_PROVIDER,
        "embed_model": embed_model,
        "rerank_provider": DOCS_RERANK_PROVIDER if DOCS_RERANK_ENABLED else None,
        "rerank_model": (
            OPENAI_RERANK_MODEL if DOCS_RERANK_PROVIDER == "openai" else DOCS_RERANK_MODEL
        ) if DOCS_RERANK_ENABLED else None,
        "llm_provider": LLM_PROVIDER,
        "generator": generator,
        "hybrid_search": HYBRID_SEARCH_ENABLED,
    }


@app.post("/search")
async def search(req: SearchRequest, request: Request):
    """Semantic retrieve + rerank — ranked chunks, no generation. The rerank is CPU-heavy,
    so /search is rate-limited and gated the same as /ask."""
    await limiter.enforce(request)
    started = time.perf_counter()
    hits = await asyncio.to_thread(_retrieve_sync, req.query, req.top_n)
    _log.info("/search completed in %.2fs query_chars=%d hits=%d", time.perf_counter() - started, len(req.query), len(hits))
    return {
        "hits": [
            {"path": h["path"], "title": h["title"], "heading": h["heading"],
             "score": round(float(h.get("rerank", h["score"])), 4)}
            for h in hits[: req.top_n]
        ]
    }


@app.post("/ask")
async def ask(req: AskRequest, request: Request):
    """Full RAG — grounded answer + cited sources."""
    # Rate-limit every caller except a trusted internal one (X-Internal-Auth secret).
    await limiter.enforce(request)
    started = time.perf_counter()
    result = await asyncio.to_thread(_query_sync, req.question, req.top_n, req.top_k)
    _log.info("/ask completed in %.2fs question_chars=%d sources=%d", time.perf_counter() - started, len(req.question), len(result["sources"]))
    return result


@app.post("/reindex", status_code=202)
async def reindex(request: Request):
    """Start a protected asynchronous scan and reindex of the configured CORPUS_DIR."""
    _require_reindex_access(request)
    try:
        job = await reindex_jobs.start()
    except ReindexInProgressError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "job_id": exc.job_id},
        ) from exc
    _log.info("/reindex accepted job_id=%s", job.id)
    return job.as_dict()


@app.get("/reindex/{job_id}")
async def reindex_status(job_id: str, request: Request):
    """Return the status and result of a previously submitted reindex job."""
    _require_reindex_access(request)
    job = reindex_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Reindex job was not found.")
    return job.as_dict()
