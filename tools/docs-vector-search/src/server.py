"""FastAPI service — /ask, /search, /health. Models load once at startup; a DB
connection is opened per request (safe under the threadpool; low concurrency on 1 vCPU).

  uvicorn src.server:app --port 8000
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import store
from .agent import query, retrieve
from .config import (
    ASK_MAX_CONCURRENCY,
    ASK_RATE_MAX,
    ASK_RATE_WINDOW_SEC,
    CORS_ORIGINS,
    EMBED_MODEL,
    QWEN_MODEL_PATH,
    RERANK_ENABLED,
    RERANK_MODEL,
    RERANK_TOP_K,
    RETRIEVE_TOP_N,
)
from .providers import embed, rerank

# Bound concurrent generation so parallel /ask calls queue instead of thrashing the
# 1 vCPU box (endpoints run in a threadpool, so a threading primitive is the right fit).
_ask_gate = threading.Semaphore(max(1, ASK_MAX_CONCURRENCY))

# Per-IP sliding-window rate limit for /ask. Public requests arrive via Caddy, which
# appends the real client to X-Forwarded-For; the internal frontend-admin proxy sends no
# XFF and is exempt. Bounded by the number of distinct client IPs seen within the window.
_rate_lock = threading.Lock()
_rate_hits: dict[str, deque] = defaultdict(deque)


def _client_ip(request: Request) -> str | None:
    """Rightmost X-Forwarded-For entry (the peer Caddy actually saw). None => no XFF =>
    an internal/trusted caller (the frontend-admin proxy), which is not rate-limited."""
    xff = request.headers.get("x-forwarded-for")
    return xff.split(",")[-1].strip() if xff else None


def _enforce_rate_limit(request: Request) -> None:
    if ASK_RATE_MAX <= 0:
        return
    ip = _client_ip(request)
    if not ip:
        return  # internal proxy caller — exempt
    now = time.monotonic()
    cutoff = now - ASK_RATE_WINDOW_SEC
    with _rate_lock:
        hits = _rate_hits[ip]
        while hits and hits[0] < cutoff:
            hits.popleft()
        if len(hits) >= ASK_RATE_MAX:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded — please wait a moment and try again.",
                headers={"Retry-After": str(ASK_RATE_WINDOW_SEC)},
            )
        hits.append(now)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm the models so the first request isn't slow, and fail fast if a model
    # or the DB is misconfigured.
    embed(["warmup"], task="query")
    if RERANK_ENABLED:
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
    question: str
    top_n: int = RETRIEVE_TOP_N
    top_k: int = RERANK_TOP_K


class SearchRequest(BaseModel):
    query: str
    top_n: int = RETRIEVE_TOP_N


@app.get("/health")
def health():
    return {
        "status": "ok",
        "loaded_chunks": getattr(app.state, "doc_count", None),
        "embed_model": EMBED_MODEL,
        "rerank_model": RERANK_MODEL if RERANK_ENABLED else None,
        "generator": QWEN_MODEL_PATH.rsplit("/", 1)[-1],
    }


@app.post("/search")
def search(req: SearchRequest):
    """Semantic retrieve + rerank — ranked chunks, no generation."""
    with store.connect() as conn:
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
    # Per-IP rate limit for public callers (via Caddy/XFF); internal proxy is exempt.
    _enforce_rate_limit(request)
    # Serialize generation (see _ask_gate) so concurrent asks can't pile up resident
    # memory on the small box; retrieval below is cheap and runs under the same gate.
    with _ask_gate, store.connect() as conn:
        return query(req.question, conn, req.top_n, req.top_k)
