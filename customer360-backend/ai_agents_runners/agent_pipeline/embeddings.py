"""Text embeddings using the existing Docs AI provider configuration."""

from __future__ import annotations

import json
import logging
import math
import os
from functools import lru_cache
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

DEFAULT_LOCAL_EMBEDDING_MODEL = (
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)
MULTILINGUAL_E5_BASE_MODEL = DEFAULT_LOCAL_EMBEDDING_MODEL


def _provider() -> str:
    value = os.environ.get("DOCS_EMBEDDING_PROVIDER", "openai").strip().lower()
    if value in {"google", "google_gemini", "google-gemini"}:
        return "gemini"
    if value not in {"openai", "gemini", "local"}:
        raise ValueError(f"Unsupported DOCS_EMBEDDING_PROVIDER: {value}")
    return value


def embedding_model_key(*, dimensions: int | None = None) -> str:
    provider = _provider()
    model = {
        "openai": os.environ.get(
            "DOCS_OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"
        ),
        "gemini": os.environ.get(
            "DOCS_GEMINI_EMBEDDING_MODEL", "gemini-embedding-001"
        ),
        "local": os.environ.get(
            "DOCS_LOCAL_EMBEDDING_MODEL",
            DEFAULT_LOCAL_EMBEDDING_MODEL,
        ),
    }[provider]
    resolved_dimensions = embedding_dimensions(dimensions)
    return f"{provider}:{model}:{resolved_dimensions}"


def embedding_dimensions(override: int | None = None) -> int:
    if override is not None:
        if (
            isinstance(override, bool)
            or not isinstance(override, int)
            or not 1 <= override <= 2000
        ):
            raise ValueError("Embedding dimensions must be between 1 and 2000")
        return override

    provider = _provider()
    value = {
        "openai": os.environ.get("DOCS_OPENAI_EMBEDDING_DIMENSIONS", "384"),
        "gemini": os.environ.get("DOCS_GEMINI_EMBEDDING_DIMENSIONS", "384"),
        "local": os.environ.get("DOCS_LOCAL_EMBEDDING_DIMENSIONS", "384"),
    }[provider]
    dimensions = int(value)
    if not 1 <= dimensions <= 2000:
        raise ValueError("Configured embedding dimensions must be between 1 and 2000")
    return dimensions


def embed_texts(
    texts: list[str],
    *,
    task: str,
    dimensions: int | None = None,
) -> list[list[float]]:
    """Embed texts using DOCS_EMBEDDING_PROVIDER and its matching settings."""
    if task not in {"query", "document"}:
        raise ValueError("Embedding task must be 'query' or 'document'")
    if not texts:
        return []
    if any(not isinstance(value, str) or not value.strip() for value in texts):
        raise ValueError("Embedding inputs must be non-blank text")

    provider = _provider()
    expected_dimensions = embedding_dimensions(dimensions)
    batches = [
        texts[start : start + 100]
        for start in range(0, len(texts), 100)
    ]
    vectors: list[list[float]] = []
    for batch in batches:
        if provider == "gemini":
            vectors.extend(
                _gemini_embeddings(batch, task=task, dimensions=expected_dimensions)
            )
        elif provider == "openai":
            vectors.extend(
                _openai_embeddings(batch, task=task, dimensions=expected_dimensions)
            )
        else:
            vectors.extend(_local_embeddings(batch, task=task))

    for vector in vectors:
        if len(vector) != expected_dimensions:
            raise ValueError(
                f"Embedding provider returned {len(vector)} dimensions; "
                f"DOCS_*_EMBEDDING_DIMENSIONS is {expected_dimensions}"
            )
        if any(not math.isfinite(value) for value in vector):
            raise ValueError("Embedding provider returned a non-finite vector value")
    if len(vectors) != len(texts):
        raise ValueError("Embedding provider did not return one vector per text")
    return vectors


def _gemini_embeddings(
    texts: list[str],
    *,
    task: str,
    dimensions: int,
) -> list[list[float]]:
    api_key = os.environ.get("DOCS_GEMINI_API_KEY", "")
    if not api_key:
        raise ValueError(
            "DOCS_EMBEDDING_PROVIDER=gemini requires DOCS_GEMINI_API_KEY"
        )

    model = os.environ.get("DOCS_GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
    task_type = "RETRIEVAL_QUERY" if task == "query" else "RETRIEVAL_DOCUMENT"
    requests = [
        {
            "model": f"models/{model}",
            "content": {"parts": [{"text": text}]},
            "taskType": task_type,
            "outputDimensionality": dimensions,
        }
        for text in texts
    ]
    base_url = os.environ.get(
        "DOCS_GEMINI_API_BASE_URL",
        "https://generativelanguage.googleapis.com/v1beta",
    ).rstrip("/")
    timeout = float(os.environ.get("DOCS_GEMINI_REQUEST_TIMEOUT_SECONDS", "120"))
    data = _post_json(
        f"{base_url}/models:batchEmbedContents?{urlencode({'key': api_key})}",
        {"requests": requests},
        timeout,
        provider="Gemini",
    )
    embeddings = data.get("embeddings")
    if not isinstance(embeddings, list) or len(embeddings) != len(texts):
        raise ValueError("Gemini embeddings response did not contain one vector per text")
    vectors: list[list[float]] = []
    for item in embeddings:
        raw_vector = item.get("values") if isinstance(item, dict) else None
        if not isinstance(raw_vector, list):
            raise ValueError("Gemini embeddings response contained an invalid vector")
        vectors.append(_coerce_float_vector(raw_vector, provider="Gemini"))
    return vectors


def _openai_embeddings(
    texts: list[str],
    *,
    task: str,
    dimensions: int,
) -> list[list[float]]:
    api_key = os.environ.get("DOCS_OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError(
            "DOCS_EMBEDDING_PROVIDER=openai requires DOCS_OPENAI_API_KEY"
        )
    model = os.environ.get(
        "DOCS_OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"
    )
    payload: dict[str, Any] = {
        "model": model,
        "input": texts,
        "dimensions": dimensions,
    }
    if "e5" in model.lower():
        prefix = "query: " if task == "query" else "passage: "
        payload["input"] = [prefix + value for value in texts]
    base_url = os.environ.get(
        "DOCS_OPENAI_API_BASE_URL", "https://api.openai.com/v1"
    ).rstrip("/")
    timeout = float(os.environ.get("DOCS_OPENAI_REQUEST_TIMEOUT_SECONDS", "120"))
    data = _post_json(
        f"{base_url}/embeddings",
        payload,
        timeout,
        provider="OpenAI-compatible",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    items = data.get("data")
    if not isinstance(items, list):
        raise ValueError("OpenAI-compatible embeddings response is missing data")
    if len(items) != len(texts) or any(not isinstance(item, dict) for item in items):
        raise ValueError("OpenAI-compatible embeddings response did not match input count")
    dict_items: list[dict[str, Any]] = [item for item in items if isinstance(item, dict)]
    if any(
        isinstance(item.get("index", 0), bool)
        or not isinstance(item.get("index", 0), int)
        for item in dict_items
    ):
        raise ValueError("OpenAI-compatible embeddings response contained an invalid index")
    dict_items.sort(key=lambda item: int(item.get("index", 0)))
    vectors: list[list[float]] = []
    for item in dict_items:
        raw_vector = item.get("embedding")
        if not isinstance(raw_vector, list):
            raise ValueError(
                "OpenAI-compatible embeddings response did not match input count"
            )
        vectors.append(
            _coerce_float_vector(raw_vector, provider="OpenAI-compatible")
        )
    return vectors


def _coerce_float_vector(raw_vector: list[Any], *, provider: str) -> list[float]:
    vector: list[float] = []
    for value in raw_vector:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{provider} embeddings response contained a non-numeric value")
        vector.append(float(value))
    return vector


@lru_cache(maxsize=1)
def _local_embedder(model: str):
    from fastembed import TextEmbedding

    models_dir = os.environ.get("MODELS_DIR", "")
    cache_dir = os.environ.get("FASTEMBED_CACHE")
    if not cache_dir and models_dir:
        cache_dir = os.path.join(models_dir, "fastembed")
    return TextEmbedding(model_name=model, cache_dir=cache_dir)


def _local_embeddings(texts: list[str], *, task: str) -> list[list[float]]:
    model = (
        os.environ.get("DOCS_LOCAL_EMBEDDING_MODEL")
        or DEFAULT_LOCAL_EMBEDDING_MODEL
    )
    if "e5" in model.lower():
        prefix = "query: " if task == "query" else "passage: "
        texts = [prefix + value for value in texts]

    vectors = _local_embedder(model).embed(texts)
    return [[float(value) for value in vector] for vector in vectors]


def _post_json(
    url: str,
    payload: dict[str, Any],
    timeout: float,
    *,
    provider: str,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"{provider} embedding request failed with HTTP {exc.code}") from exc
    except (URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{provider} embedding request failed: {exc}") from exc
    if not isinstance(result, dict):
        raise ValueError(f"{provider} embedding response must be a JSON object")
    return result
