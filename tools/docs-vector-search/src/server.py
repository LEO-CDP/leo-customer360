"""FastAPI service — /ask, /search, /health. Models load once at startup; a DB
connection is opened per request (safe under the threadpool; low concurrency on 1 vCPU).

  uvicorn src.server:app --port 8000
"""
from __future__ import annotations

import hmac
import logging
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import store
from .agent import query, retrieve
from .config import (
    ASK_MAX_CONCURRENCY,
    ASK_RATE_MAX,
    ASK_RATE_WINDOW_SEC,
    CORS_ORIGINS,
    EMBED_MODEL,
    DOCS_LOCAL_MODEL_PATH,
    DOCS_RERANK_ENABLED,
    DOCS_RERANK_MODEL,
    INTERNAL_API_SECRET,
    QUESTION_MAX_LEN,
    RERANK_TOP_K,
    RETRIEVE_TOP_N,
    TOP_K_MAX,
    TOP_N_MAX,
    TRUSTED_PROXY_HOPS,
    WEB_CONCURRENCY,
)
from .providers import embed, rerank

_log = logging.getLogger("uvicorn.error")

# The limiter and gate are per-process in-memory. With >1 worker each process keeps its
# own copy, so the aggregate = per-worker budget × workers. Divide the budgets by the
# worker count to keep the aggregate near target (an exact cross-worker limit needs a
# shared store such as Redis; see the config note).
_EFFECTIVE_MAX_CONCURRENCY = max(1, ASK_MAX_CONCURRENCY // WEB_CONCURRENCY)
_EFFECTIVE_RATE_MAX = max(1, ASK_RATE_MAX // WEB_CONCURRENCY) if ASK_RATE_MAX > 0 else 0

# Bound concurrent generation/retrieval so parallel calls queue instead of thrashing the
# 1 vCPU box (endpoints run in a threadpool, so a threading primitive is the right fit).
_ask_gate = threading.Semaphore(_EFFECTIVE_MAX_CONCURRENCY)

# Per-IP sliding-window rate limit for the CPU-heavy endpoints (/ask + /search). Bounded
# to clients active within the window: expired buckets are swept (see _enforce_rate_limit).
_rate_lock = threading.Lock()
_rate_hits: dict[str, deque] = defaultdict(deque)
_last_sweep = 0.0


def _is_internal(request: Request) -> bool:
    """A trusted internal caller (the frontend-admin proxy) is identified by presenting
    INTERNAL_API_SECRET in X-Internal-Auth — NEVER by the absence of X-Forwarded-For. No
    secret configured => nobody is exempt (every request is rate-limited)."""
    if not INTERNAL_API_SECRET:
        return False
    return hmac.compare_digest(request.headers.get("x-internal-auth", ""), INTERNAL_API_SECRET)


def _client_ip(request: Request) -> str:
    """Client identity for rate limiting, taken from the trusted X-Forwarded-For hop. If
    the header is absent or the chain is shorter than TRUSTED_PROXY_HOPS (the request did
    not traverse the trusted proxy — an exposed port, SSRF, a nested proxy), fall back to
    the direct peer and STILL limit it. Never returns None: absence of forwarding info must
    not exempt a caller (fail closed)."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        entries = [e.strip() for e in xff.split(",") if e.strip()]
        if len(entries) >= TRUSTED_PROXY_HOPS:
            return entries[-TRUSTED_PROXY_HOPS]
    return request.client.host if request.client else "unknown"


def _enforce_rate_limit(request: Request) -> None:
    if _EFFECTIVE_RATE_MAX <= 0 or _is_internal(request):
        return
    global _last_sweep
    ip = _client_ip(request)
    now = time.monotonic()
    cutoff = now - ASK_RATE_WINDOW_SEC
    with _rate_lock:
        # Sweep buckets whose newest hit has expired, at most once per window, so the map
        # stays bounded by currently-active clients rather than every IP ever seen.
        if now - _last_sweep >= ASK_RATE_WINDOW_SEC:
            _last_sweep = now
            for stale in [k for k, d in _rate_hits.items() if not d or d[-1] < cutoff]:
                del _rate_hits[stale]
        hits = _rate_hits[ip]
        while hits and hits[0] < cutoff:
            hits.popleft()
        if len(hits) >= _EFFECTIVE_RATE_MAX:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded — please wait a moment and try again.",
                headers={"Retry-After": str(ASK_RATE_WINDOW_SEC)},
            )
        hits.append(now)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if WEB_CONCURRENCY > 1:
        _log.warning(
            "WEB_CONCURRENCY=%d: the rate limiter and concurrency gate are per-process; "
            "per-worker budgets were divided (rate=%d/worker, concurrency=%d/worker) to keep "
            "the aggregate near target. Run a single worker or use a shared store for an exact "
            "limit.", WEB_CONCURRENCY, _EFFECTIVE_RATE_MAX, _EFFECTIVE_MAX_CONCURRENCY,
        )
    # Warm the models so the first request isn't slow, and fail fast if a model
    # or the DB is misconfigured.
    embed(["warmup"], task="query")
    if DOCS_RERANK_ENABLED:
        rerank("warmup", ["warmup"])
    with store.connect() as conn:
        app.state.doc_count = store.count(conn)
    yield


app = FastAPI(title="LEO Customer 360 — Document Vector Search", lifespan=lifespan)

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


@app.get("/health")
def health():
    return {
        "status": "ok",
        "loaded_chunks": getattr(app.state, "doc_count", None),
        "embed_model": EMBED_MODEL,
        "rerank_model": DOCS_RERANK_MODEL if DOCS_RERANK_ENABLED else None,
        "generator": DOCS_LOCAL_MODEL_PATH.rsplit("/", 1)[-1],
    }


@app.post("/search")
def search(req: SearchRequest, request: Request):
    """Semantic retrieve + rerank — ranked chunks, no generation. The rerank is CPU-heavy,
    so /search is rate-limited and gated the same as /ask."""
    _enforce_rate_limit(request)
    with _ask_gate, store.connect() as conn:
        hits = retrieve(req.query, conn, req.top_n)
    return {
        "hits": [
            {"path": h["path"], "title": h["title"], "heading": h["heading"],
             "score": round(float(h.get("rerank", h["score"])), 4)}
            for h in hits[: req.top_n]
        ]
    }


@app.post("/ask")
def ask(req: AskRequest, request: Request):
    """Full RAG — grounded answer + cited sources."""
    # Rate-limit every caller except a trusted internal one (X-Internal-Auth secret).
    _enforce_rate_limit(request)
    # Serialize generation (see _ask_gate) so concurrent asks can't pile up resident
    # memory on the small box; retrieval below is cheap and runs under the same gate.
    with _ask_gate, store.connect() as conn:
        return query(req.question, conn, req.top_n, req.top_k)
