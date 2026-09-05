"""Runtime configuration, driven by environment variables (and an optional .env)."""
from __future__ import annotations

import os
from pathlib import Path

try:  # optional — .env is convenient in dev, not required in CI/containers
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except Exception:  # noqa: BLE001 — dotenv absent is fine
    pass

# src/config.py → src → docs-vector-search → tools → <repo root>
REPO_ROOT = Path(__file__).resolve().parents[3]
PKG_ROOT = Path(__file__).resolve().parents[1]

# OpenAI is the only provider (for now). The SDK only auto-reads OPENAI_API_KEY,
# so also accept the project's CI secret name LEO_OPENAI_API_KEY.
OPENAI_API_KEY = os.getenv("LEO_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")

# Chat model: prefer the CI variable LEO_OPENAI_MODEL_NAME, then CHAT_MODEL, then a default.
CHAT_MODEL = os.getenv("LEO_OPENAI_MODEL_NAME") or os.getenv("CHAT_MODEL", "gpt-4.1")

# Embeddings model (documents and queries must use the same one).
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")

# Corpus + index location.
CORPUS_DIR = Path(os.getenv("CORPUS_DIR", REPO_ROOT / "docs")).resolve()
DATA_DIR = Path(os.getenv("DATA_DIR", PKG_ROOT / "data")).resolve()
INDEX_PATH = DATA_DIR / "embeddings.json"

# Retrieval knobs.
TOP_K = int(os.getenv("TOP_K", "6"))
HOPS = int(os.getenv("HOPS", "1"))
CONTEXT_CHAR_BUDGET = int(os.getenv("CONTEXT_CHAR_BUDGET", "24000"))
