"""FastAPI service — /ask, /search, /health. The index is loaded once at startup.

  uvicorn src.server:app --port 8000
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from .agent import query
from .config import CHAT_MODEL, EMBED_MODEL, HOPS, TOP_K
from .retriever import Index

_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    t0 = time.time()
    _state["index"] = Index.load()
    _state["build_seconds"] = round(time.time() - t0, 2)
    yield
    _state.clear()


app = FastAPI(title="LEO Customer 360 — Docs Vector Search", lifespan=lifespan)


class AskRequest(BaseModel):
    question: str
    top_k: int = TOP_K
    hops: int = HOPS


class SearchRequest(BaseModel):
    query: str
    top_k: int = TOP_K


@app.get("/health")
def health():
    index: Index | None = _state.get("index")
    return {
        "status": "ok" if index else "loading",
        "loaded_docs": len(index.docs) if index else 0,
        "chat_model": CHAT_MODEL,
        "embed_model": EMBED_MODEL,
        "build_seconds": _state.get("build_seconds"),
    }


@app.post("/search")
def search(req: SearchRequest):
    """Pure semantic search — ranked docs, no LLM call (cheap)."""
    hits = _state["index"].search(req.query, req.top_k)
    return {
        "hits": [{"path": d.path, "title": d.title, "score": round(s, 4)} for d, s in hits]
    }


@app.post("/ask")
def ask(req: AskRequest):
    """Graph-RAG — grounded answer + cited sources (calls the chat model)."""
    return query(req.question, _state["index"], req.top_k, req.hops)
