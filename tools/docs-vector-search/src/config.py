"""Runtime configuration for the provider-agnostic docs RAG service."""
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

def _provider(value: str) -> str:
    aliases = {
        "google": "gemini",
        "google_gemini": "gemini",
        "google-gemini": "gemini",
    }
    normalized = value.strip().lower()
    return aliases.get(normalized, normalized)


# Embedding — hosted OpenAI by default. Each provider owns its model and vector
# dimension; the active values are selected from the provider-specific settings.
EMBED_PROVIDER = _provider(
    os.getenv("DOCS_EMBEDDING_PROVIDER", "openai")
)
OPENAI_EMBEDDING_MODEL = os.getenv(
    "DOCS_OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"
)
OPENAI_EMBEDDING_DIMENSIONS = int(
    os.getenv("DOCS_OPENAI_EMBEDDING_DIMENSIONS", "384")
)
GEMINI_EMBEDDING_MODEL = os.getenv(
    "DOCS_GEMINI_EMBEDDING_MODEL", "gemini-embedding-001"
)
GEMINI_EMBEDDING_DIMENSIONS = int(
    os.getenv("DOCS_GEMINI_EMBEDDING_DIMENSIONS", "384")
)
LOCAL_EMBEDDING_MODEL = os.getenv(
    "DOCS_LOCAL_EMBEDDING_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
LOCAL_EMBEDDING_DIMENSIONS = int(os.getenv("DOCS_LOCAL_EMBEDDING_DIMENSIONS", "384"))

_EMBEDDING_MODELS = {
    "openai": (OPENAI_EMBEDDING_MODEL, OPENAI_EMBEDDING_DIMENSIONS),
    "gemini": (GEMINI_EMBEDDING_MODEL, GEMINI_EMBEDDING_DIMENSIONS),
    "local": (LOCAL_EMBEDDING_MODEL, LOCAL_EMBEDDING_DIMENSIONS),
}
if EMBED_PROVIDER not in _EMBEDDING_MODELS:
    raise ValueError(f"Unsupported DOCS_EMBEDDING_PROVIDER: {EMBED_PROVIDER}")
EMBED_MODEL, EMBED_DIM = _EMBEDDING_MODELS[EMBED_PROVIDER]

# Reranking — hosted OpenAI by default, or local via fastembed TextCrossEncoder.
DOCS_RERANK_ENABLED = os.getenv("DOCS_RERANK_ENABLED", "true").lower() == "true"
DOCS_RERANK_PROVIDER = _provider(os.getenv("DOCS_RERANK_PROVIDER", "openai"))
DOCS_RERANK_MODEL = os.getenv("DOCS_RERANK_MODEL", "BAAI/bge-reranker-base")

# Generation — OpenAI by default. Each provider owns its model settings.
LLM_PROVIDER = _provider(
    os.getenv("DOCS_LLM_PROVIDER", "openai")
)
DOCS_LLM_MAX_OUTPUT_TOKENS = int(os.getenv("DOCS_LLM_MAX_OUTPUT_TOKENS", "256"))
LOCAL_LLM_CONTEXT_TOKENS = int(os.getenv("DOCS_LOCAL_LLM_CONTEXT_TOKENS", "2048"))
LOCAL_LLM_THREADS = int(os.getenv("DOCS_LOCAL_LLM_THREADS", "2"))
LOCAL_LLM_BATCH_SIZE = int(os.getenv("DOCS_LOCAL_LLM_BATCH_SIZE", "512"))
LOCAL_LLM_GPU_LAYERS = int(os.getenv("DOCS_LOCAL_LLM_GPU_LAYERS", "-1"))
LOCAL_LLM_MODEL_PATH = os.getenv(
    "DOCS_LOCAL_LLM_MODEL_PATH",
    str(MODELS_DIR / "Qwen2.5-0.5B-Instruct-Q4_K_M.gguf"),
)
OPENAI_LLM_MODEL = os.getenv("DOCS_OPENAI_LLM_MODEL", "gpt-5.6-luna")
OPENAI_RERANK_MODEL = os.getenv("DOCS_OPENAI_RERANK_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = (
    os.getenv("DOCS_OPENAI_API_KEY", "")
)
OPENAI_API_BASE_URL = os.getenv(
    "DOCS_OPENAI_API_BASE_URL", "https://api.openai.com/v1"
).rstrip("/")
OPENAI_REQUEST_TIMEOUT_SECONDS = float(
    os.getenv("DOCS_OPENAI_REQUEST_TIMEOUT_SECONDS", "120")
)
OPENAI_RERANK_TIMEOUT_SECONDS = float(
    os.getenv("DOCS_OPENAI_RERANK_TIMEOUT_SECONDS", "8")
)
DOCS_RERANK_OPENAI_FALLBACK = _provider(
    os.getenv("DOCS_RERANK_OPENAI_FALLBACK", "vector")
)
GEMINI_LLM_MODEL = os.getenv("DOCS_GEMINI_LLM_MODEL", "gemini-2.5-flash")
GEMINI_API_KEY = (
    os.getenv("DOCS_GEMINI_API_KEY", "")
)
GEMINI_API_BASE_URL = (
    os.getenv(
        "DOCS_GEMINI_API_BASE_URL",
        "https://generativelanguage.googleapis.com/v1beta",
    )
).rstrip("/")
GEMINI_REQUEST_TIMEOUT_SECONDS = float(
    os.getenv("DOCS_GEMINI_REQUEST_TIMEOUT_SECONDS", "120")
)

if LLM_PROVIDER not in {"openai", "gemini", "local"}:
    raise ValueError(f"Unsupported DOCS_LLM_PROVIDER: {LLM_PROVIDER}")
if DOCS_RERANK_PROVIDER not in {"openai", "local"}:
    raise ValueError(f"Unsupported DOCS_RERANK_PROVIDER: {DOCS_RERANK_PROVIDER}")
if DOCS_RERANK_OPENAI_FALLBACK not in {"local", "vector"}:
    raise ValueError(
        f"Unsupported DOCS_RERANK_OPENAI_FALLBACK: {DOCS_RERANK_OPENAI_FALLBACK}"
    )

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
# Per-IP and browser sliding-window rate limit for the CPU-heavy endpoints (/ask and
# /search). Redis is the source of truth so limits work across workers and replicas.
# 0 disables the limiter; otherwise Redis must be reachable.
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

# Redis-backed request limiting. The local Docker stack resolves ``redis`` on the shared
# customer360-network; production deployment supplies the API-box Redis address.
DOCS_REDIS_HOST = os.getenv("DOCS_REDIS_HOST", "redis")
DOCS_REDIS_PORT = int(os.getenv("DOCS_REDIS_PORT", "6580"))
DOCS_REDIS_DB = int(os.getenv("DOCS_REDIS_DB", "0"))
DOCS_REDIS_PASSWORD = os.getenv("DOCS_REDIS_PASSWORD") or os.getenv("REDIS_PASSWORD", "")
DOCS_REDIS_CONNECT_TIMEOUT_SECONDS = float(
    os.getenv("DOCS_REDIS_CONNECT_TIMEOUT_SECONDS", "1")
)
DOCS_REDIS_SOCKET_TIMEOUT_SECONDS = float(
    os.getenv("DOCS_REDIS_SOCKET_TIMEOUT_SECONDS", "1")
)

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
