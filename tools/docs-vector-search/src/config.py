"""Runtime configuration (env-driven, with an optional .env). Fully local models
+ pgvector on the vDB."""
from __future__ import annotations

import os
from pathlib import Path

try:  # optional in CI/containers
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except Exception:  # noqa: BLE001
    pass

# Local layout: src/config.py → src → docs-vector-search → tools → <repo root>.
# In the container the code lives at /app/src, so parents[3] doesn't exist — fall back
# safely (CORPUS_DIR/MODELS_DIR are set via env there, so this default is never used).
_HERE = Path(__file__).resolve()
REPO_ROOT = _HERE.parents[3] if len(_HERE.parents) > 3 else _HERE.parent
PKG_ROOT = _HERE.parents[1]

# Where model weights (fastembed ONNX + Qwen GGUF) live — a volume in the container.
MODELS_DIR = Path(os.getenv("MODELS_DIR", PKG_ROOT / "models")).resolve()
# Overridable so the image can point it at a baked path (models pre-fetched at build,
# outside the mounted MODELS_DIR) — see the Dockerfile. Local default: under MODELS_DIR.
FASTEMBED_CACHE = os.getenv("FASTEMBED_CACHE", str(MODELS_DIR / "fastembed"))

# Corpus
CORPUS_DIR = Path(os.getenv("CORPUS_DIR", REPO_ROOT / "docs")).resolve()

# Chunking (approximate tokens; ~4 chars/token)
CHUNK_TOKENS = int(os.getenv("CHUNK_TOKENS", "400"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "50"))

# Embedding — local fastembed by default. OpenAI-compatible embeddings are optional;
# keep EMBED_DIM aligned with the existing pgvector column when switching providers.
EMBED_PROVIDER = (os.getenv("DOCS_EMBEDDING_PROVIDER") or os.getenv("EMBED_PROVIDER", "local")).lower()
EMBED_MODEL = os.getenv(
    "DOCS_EMBEDDING_MODEL",
    os.getenv("EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"),
)
EMBED_DIM = int(os.getenv("DOCS_EMBEDDING_DIMENSIONS") or os.getenv("EMBED_DIM", "384"))
OPENAI_EMBEDDING_MODEL = os.getenv(
    "DOCS_OPENAI_EMBEDDING_MODEL",
    os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
)
OPENAI_EMBEDDING_DIMENSIONS = int(
    os.getenv("DOCS_OPENAI_EMBEDDING_DIMENSIONS")
    or os.getenv("OPENAI_EMBEDDING_DIMENSIONS", str(EMBED_DIM))
)

# Reranking — local, via fastembed TextCrossEncoder.
DOCS_RERANK_ENABLED = (
    os.getenv("DOCS_RERANK_ENABLED") or os.getenv("RERANK_ENABLED", "true")
).lower() == "true"
DOCS_RERANK_MODEL = os.getenv(
    "DOCS_RERANK_MODEL", os.getenv("RERANK_MODEL", "BAAI/bge-reranker-base")
)

# Generation — local Llama GGUF by default, or an OpenAI-compatible chat model.
LLM_PROVIDER = (os.getenv("DOCS_LLM_PROVIDER") or os.getenv("LLM_PROVIDER", "local")).lower()
DOCS_LOCAL_MODEL_PATH = os.getenv(
    "DOCS_LOCAL_MODEL_PATH",
    os.getenv("QWEN_MODEL_PATH", str(MODELS_DIR / "Qwen2.5-0.5B-Instruct-Q4_K_M.gguf")),
)
GEN_MAX_TOKENS = int(
    os.getenv("DOCS_GENERATION_MAX_TOKENS") or os.getenv("GEN_MAX_TOKENS", "256")
)
GEN_CTX = int(
    os.getenv("DOCS_GENERATION_CONTEXT_TOKENS") or os.getenv("GEN_CTX", "2048")
)
DOCS_LLM_THREADS = int(os.getenv("DOCS_LLM_THREADS") or os.getenv("GEN_THREADS", "2"))
DOCS_LLM_BATCH_SIZE = int(
    os.getenv("DOCS_LLM_BATCH_SIZE") or os.getenv("LLAMA_N_BATCH", "512")
)
LLAMA_N_GPU_LAYERS = int(os.getenv("LLAMA_N_GPU_LAYERS", "-1"))
OPENAI_CHAT_MODEL = os.getenv("DOCS_LLM_MODEL") or os.getenv("OPENAI_CHAT_MODEL") or os.getenv(
    "LEO_OPENAI_MODEL_NAME", "gpt-5.6-luna"
)
OPENAI_API_KEY = (
    os.getenv("DOCS_OPENAI_API_KEY")
    or os.getenv("OPENAI_API_KEY")
    or os.getenv("LEO_OPENAI_API_KEY", "")
)
OPENAI_BASE_URL = (
    os.getenv("DOCS_OPENAI_BASE_URL")
    or os.getenv("OPENAI_BASE_URL")
    or os.getenv("LEO_OPENAI_BASE_URL", "https://api.openai.com/v1")
).rstrip("/")
OPENAI_TIMEOUT = float(os.getenv("OPENAI_TIMEOUT", "120"))

# Retrieval
# Candidate pool the reranker sees. Kept wide: short / low-signal queries (esp. bare
# Vietnamese like "persona là gì vậy?") rank the right chunk at vector-position 20–50, so
# a pool of 20 starved the reranker and the answer fell back to "I don't know". top_k (what
# the generator reads) is unchanged — this only widens what the reranker can pick from.
RETRIEVE_TOP_N = int(os.getenv("RETRIEVE_TOP_N", "50"))
RERANK_TOP_K = int(os.getenv("RERANK_TOP_K", "5"))
CONTEXT_CHAR_BUDGET = int(os.getenv("CONTEXT_CHAR_BUDGET", "6000"))  # small — 0.5B ctx

# --- HTTP / browser access -------------------------------------------------------
# CORS allow-list for browsers calling this API directly (the static docs site on
# GitHub Pages). Server-side callers (the frontend-admin /ai proxy) are same-origin
# and don't need this. Comma-separated exact origins; never "*" on an unauthenticated,
# CPU-heavy endpoint. Default: the public docs site.
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "https://leo-cdp.github.io").split(",")
    if o.strip()
]
# Serialize expensive generation so concurrent /ask + /search calls queue instead of
# thrashing (and OOM-ing) the 1 vCPU box.
ASK_MAX_CONCURRENCY = int(os.getenv("ASK_MAX_CONCURRENCY", "1"))
# Per-IP sliding-window rate limit for the CPU-heavy endpoints (/ask and /search).
# Applied to every caller EXCEPT a trusted internal one that presents INTERNAL_API_SECRET
# (see below). 0 disables. The client IP is derived from a trusted X-Forwarded-For hop
# (TRUSTED_PROXY_HOPS); when forwarding info is missing/ambiguous the direct peer is used
# and the request is still limited (fail closed) — absence of a header never exempts.
ASK_RATE_MAX = int(os.getenv("ASK_RATE_MAX", "10"))
ASK_RATE_WINDOW_SEC = int(os.getenv("ASK_RATE_WINDOW_SEC", "60"))
# Number of trusted reverse-proxy hops that append to X-Forwarded-For (Caddy = 1). The
# real client is the Nth entry from the right. A chain shorter than this is treated as
# not-via-the-proxy and falls back to the direct peer (fail closed).
TRUSTED_PROXY_HOPS = max(1, int(os.getenv("TRUSTED_PROXY_HOPS", "1")))
# Shared secret that identifies a trusted internal caller (the frontend-admin proxy),
# presented as the X-Internal-Auth header. EMPTY (default) => no caller is ever exempt,
# so admin traffic is rate-limited too. Set the SAME value here and as the proxy's
# DOCS_INTERNAL_SECRET to exempt the admin console from the public rate limit.
INTERNAL_API_SECRET = os.getenv("INTERNAL_API_SECRET", "")
# Worker/process count (uvicorn/gunicorn WEB_CONCURRENCY convention). The rate limiter and
# concurrency gate are per-process in-memory, so per-worker budgets are divided by this to
# keep the aggregate near ASK_RATE_MAX / ASK_MAX_CONCURRENCY. For an exact shared limit
# across workers, run a single worker or back the limiter with a shared store (Redis).
WEB_CONCURRENCY = max(1, int(os.getenv("WEB_CONCURRENCY", "1")))

# Request-parameter caps (defence against CPU amplification via huge/negative values).
TOP_N_MAX = int(os.getenv("TOP_N_MAX", "50"))
TOP_K_MAX = int(os.getenv("TOP_K_MAX", "20"))
# Max accepted question/query length (chars). Mirrors the frontend-admin proxy's cap.
QUESTION_MAX_LEN = int(os.getenv("QUESTION_MAX_LEN", "2000"))

# Vector store — pgvector on the VNGCloud vDB (PostgreSQL 15)
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = int(os.getenv("PG_PORT", "5432"))
PG_DATABASE = os.getenv("PG_DATABASE", "postgres")
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASSWORD = os.getenv("PG_PASSWORD", "")
PG_SCHEMA = os.getenv("PG_SCHEMA", "rag")


def pg_dsn() -> str:
    return (
        f"host={PG_HOST} port={PG_PORT} dbname={PG_DATABASE} "
        f"user={PG_USER} password={PG_PASSWORD}"
    )
