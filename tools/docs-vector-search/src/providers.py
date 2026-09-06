"""Local-model seams — the only code that loads a model. All lazy-loaded once.

  embed()    -> fastembed paraphrase-multilingual-MiniLM (ONNX)  [384-dim VN+EN]
  rerank()   -> fastembed bge-reranker-base (ONNX)  [cross-encoder]
  generate() -> llama-cpp-python Qwen2.5-0.5B (GGUF)
"""
from __future__ import annotations

import functools

from .config import (
    EMBED_MODEL,
    FASTEMBED_CACHE,
    GEN_CTX,
    GEN_MAX_TOKENS,
    GEN_THREADS,
    QWEN_MODEL_PATH,
    RERANK_MODEL,
)


# --------------------------------------------------------------- embed (e5)
@functools.lru_cache(maxsize=1)
def _embedder():
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=EMBED_MODEL, cache_dir=FASTEMBED_CACHE)


# e5 models need "query:"/"passage:" task prefixes; other supported models
# (e.g. sentence-transformers/paraphrase-multilingual-MiniLM) must NOT get them.
_E5_PREFIX = "e5" in EMBED_MODEL.lower()


def embed(texts: list[str], *, task: str = "document") -> list[list[float]]:
    """Embed texts. For e5 models, prepend the task prefix (`query:` for the
    question, `passage:` for documents); the same model must embed both."""
    if not texts:
        return []
    if _E5_PREFIX:
        prefix = "query: " if task == "query" else "passage: "
        texts = [prefix + t for t in texts]
    return [[float(x) for x in v] for v in _embedder().embed(texts)]


# ------------------------------------------------------------ rerank (bge)
@functools.lru_cache(maxsize=1)
def _reranker():
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    return TextCrossEncoder(model_name=RERANK_MODEL, cache_dir=FASTEMBED_CACHE)


def rerank(query: str, passages: list[str]) -> list[float]:
    """Cross-encoder relevance scores (higher = more relevant), one per passage."""
    if not passages:
        return []
    return [float(s) for s in _reranker().rerank(query, passages)]


# ---------------------------------------------------------- generate (Qwen)
@functools.lru_cache(maxsize=1)
def _llm():
    from llama_cpp import Llama

    return Llama(
        model_path=QWEN_MODEL_PATH,
        n_ctx=GEN_CTX,
        n_threads=GEN_THREADS,
        verbose=False,
    )


def generate(system: str, user: str) -> str:
    resp = _llm().create_chat_completion(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=GEN_MAX_TOKENS,
        temperature=0.2,
    )
    return resp["choices"][0]["message"]["content"].strip()
