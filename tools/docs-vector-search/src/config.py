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
FASTEMBED_CACHE = str(MODELS_DIR / "fastembed")

# Corpus
CORPUS_DIR = Path(os.getenv("CORPUS_DIR", REPO_ROOT / "docs")).resolve()

# Chunking (approximate tokens; ~4 chars/token)
CHUNK_TOKENS = int(os.getenv("CHUNK_TOKENS", "400"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "50"))

# Embedding — local, via fastembed (ONNX). e5 needs query:/passage: prefixes.
EMBED_MODEL = os.getenv("EMBED_MODEL", "intfloat/multilingual-e5-small")
EMBED_DIM = int(os.getenv("EMBED_DIM", "384"))

# Reranking — local, via fastembed TextCrossEncoder.
RERANK_ENABLED = os.getenv("RERANK_ENABLED", "true").lower() == "true"
RERANK_MODEL = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-base")

# Generation — local, via llama-cpp-python (GGUF).
QWEN_MODEL_PATH = os.getenv(
    "QWEN_MODEL_PATH", str(MODELS_DIR / "Qwen2.5-0.5B-Instruct-Q4_K_M.gguf")
)
GEN_MAX_TOKENS = int(os.getenv("GEN_MAX_TOKENS", "512"))
GEN_CTX = int(os.getenv("GEN_CTX", "4096"))
GEN_THREADS = int(os.getenv("GEN_THREADS", "0")) or None  # None → llama default

# Retrieval
RETRIEVE_TOP_N = int(os.getenv("RETRIEVE_TOP_N", "20"))
RERANK_TOP_K = int(os.getenv("RERANK_TOP_K", "5"))
CONTEXT_CHAR_BUDGET = int(os.getenv("CONTEXT_CHAR_BUDGET", "6000"))  # small — 0.5B ctx

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
