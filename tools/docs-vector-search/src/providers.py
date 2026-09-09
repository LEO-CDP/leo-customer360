"""Embedding, reranking, and generation provider seams.

Local models remain the default. OpenAI-compatible embeddings and chat generation
are opt-in through EMBED_PROVIDER=openai and LLM_PROVIDER=openai. Keeping the
switches here means the retrieval and agent layers do not depend on a provider.
"""
from __future__ import annotations

import functools
import json
import logging
import os
import shutil
import subprocess
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import (
    EMBED_PROVIDER,
    EMBED_DIM,
    EMBED_MODEL,
    FASTEMBED_CACHE,
    GEN_CTX,
    GEN_MAX_TOKENS,
    DOCS_LLM_BATCH_SIZE,
    DOCS_LLM_THREADS,
    LLAMA_N_GPU_LAYERS,
    LLM_PROVIDER,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_CHAT_MODEL,
    OPENAI_EMBEDDING_DIMENSIONS,
    OPENAI_EMBEDDING_MODEL,
    OPENAI_TIMEOUT,
    DOCS_LOCAL_MODEL_PATH,
    DOCS_RERANK_MODEL,
)

_log = logging.getLogger(__name__)


def _openai_request(path: str, payload: dict) -> dict:
    if not OPENAI_API_KEY:
        raise RuntimeError("OpenAI provider selected but OPENAI_API_KEY is not configured")

    request = Request(
        f"{OPENAI_BASE_URL}/{path.lstrip('/')}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=OPENAI_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as exc:
        detail = getattr(exc, "reason", exc)
        raise RuntimeError(f"OpenAI request to {path} failed: {detail}") from exc


def _openai_embeddings(texts: list[str], *, task: str) -> list[list[float]]:
    payload = {
        "model": OPENAI_EMBEDDING_MODEL,
        "input": texts,
    }
    if OPENAI_EMBEDDING_DIMENSIONS > 0:
        payload["dimensions"] = OPENAI_EMBEDDING_DIMENSIONS
    if "e5" in OPENAI_EMBEDDING_MODEL.lower():
        prefix = "query: " if task == "query" else "passage: "
        payload["input"] = [prefix + text for text in texts]

    response = _openai_request("embeddings", payload)
    items = sorted(response.get("data", []), key=lambda item: item.get("index", 0))
    vectors = [item.get("embedding") for item in items]
    if len(vectors) != len(texts) or any(not isinstance(vector, list) for vector in vectors):
        raise RuntimeError("OpenAI embeddings response did not contain one vector per input")
    converted = [[float(value) for value in vector] for vector in vectors]
    if any(len(vector) != EMBED_DIM for vector in converted):
        actual = len(converted[0]) if converted else 0
        raise RuntimeError(
            f"OpenAI embedding dimension {actual} does not match EMBED_DIM={EMBED_DIM}"
        )
    return converted


# --------------------------------------------------------------- embed
@functools.lru_cache(maxsize=1)
def _embedder():
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=EMBED_MODEL, cache_dir=FASTEMBED_CACHE)


# e5 models need "query:"/"passage:" task prefixes; other supported models
# (e.g. sentence-transformers/paraphrase-multilingual-MiniLM) must NOT get them.
_E5_PREFIX = "e5" in EMBED_MODEL.lower()


def embed(texts: list[str], *, task: str = "document") -> list[list[float]]:
    """Embed texts through the configured local or OpenAI provider."""
    if not texts:
        return []
    if EMBED_PROVIDER == "openai":
        return _openai_embeddings(texts, task=task)
    if EMBED_PROVIDER != "local":
        raise RuntimeError(f"Unsupported EMBED_PROVIDER: {EMBED_PROVIDER}")
    if _E5_PREFIX:
        prefix = "query: " if task == "query" else "passage: "
        texts = [prefix + t for t in texts]
    return [[float(x) for x in v] for v in _embedder().embed(texts)]


# ------------------------------------------------------------ rerank (bge)
@functools.lru_cache(maxsize=1)
def _reranker():
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    return TextCrossEncoder(model_name=DOCS_RERANK_MODEL, cache_dir=FASTEMBED_CACHE)


def rerank(query: str, passages: list[str]) -> list[float]:
    """Cross-encoder relevance scores (higher = more relevant), one per passage."""
    if not passages:
        return []
    return [float(s) for s in _reranker().rerank(query, passages)]


# ---------------------------------------------------------- generate
def _cuda_device_available() -> bool:
    if os.path.exists("/dev/nvidia0"):
        return True
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return False
    try:
        return subprocess.run(
            [nvidia_smi, "-L"], capture_output=True, text=True, timeout=3, check=False
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@functools.lru_cache(maxsize=1)
def _llm():
    from llama_cpp import Llama

    gpu_layers = 0
    supports_gpu = False
    try:
        from llama_cpp import llama_supports_gpu_offload

        supports_gpu = bool(llama_supports_gpu_offload())
    except ImportError:
        pass
    if LLAMA_N_GPU_LAYERS != 0 and _cuda_device_available() and supports_gpu:
        gpu_layers = LLAMA_N_GPU_LAYERS
        _log.info("Using GPU offload for local Llama (%s layers)", gpu_layers)
    elif LLAMA_N_GPU_LAYERS != 0 and _cuda_device_available() and not supports_gpu:
        _log.warning("NVIDIA GPU detected, but llama-cpp-python has no GPU backend; using CPU")

    return Llama(
        model_path=DOCS_LOCAL_MODEL_PATH,
        n_ctx=GEN_CTX,
        n_threads=DOCS_LLM_THREADS,
        n_threads_batch=DOCS_LLM_THREADS,
        n_batch=DOCS_LLM_BATCH_SIZE,
        n_gpu_layers=gpu_layers,
        use_mmap=True,
        verbose=False,
    )


def generate(system: str, user: str) -> str:
    """Generate an answer through the configured local or OpenAI provider."""
    if LLM_PROVIDER == "openai":
        payload = {
            "model": OPENAI_CHAT_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if OPENAI_CHAT_MODEL.lower().startswith("gpt-5"):
            payload["max_completion_tokens"] = GEN_MAX_TOKENS
        else:
            payload["max_tokens"] = GEN_MAX_TOKENS
            payload["temperature"] = 0.2
        response = _openai_request("chat/completions", payload)
        try:
            return response["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, AttributeError) as exc:
            raise RuntimeError("OpenAI chat response did not contain an answer") from exc
    if LLM_PROVIDER != "local":
        raise RuntimeError(f"Unsupported LLM_PROVIDER: {LLM_PROVIDER}")

    resp = _llm().create_chat_completion(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=GEN_MAX_TOKENS,
        temperature=0.2,
    )
    return resp["choices"][0]["message"]["content"].strip()
