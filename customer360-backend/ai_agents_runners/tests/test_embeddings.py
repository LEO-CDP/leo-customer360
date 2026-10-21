import pytest

from ai_agents_runners.agent_pipeline import embeddings


def test_gemini_embeddings_use_existing_docs_settings_and_retrieval_tasks(monkeypatch):
    monkeypatch.setenv("DOCS_EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("DOCS_GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("DOCS_GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
    monkeypatch.setenv("DOCS_GEMINI_EMBEDDING_DIMENSIONS", "384")
    requests = []

    def fake_post(url, payload, timeout, **kwargs):
        requests.append((url, payload, timeout, kwargs))
        return {
            "embeddings": [
                {"values": [0.25] * 384}
                for _ in payload["requests"]
            ]
        }

    monkeypatch.setattr(embeddings, "_post_json", fake_post)
    vectors = embeddings.embed_texts(["Customer interest", "Content title"], task="query")

    assert len(vectors) == 2
    assert len(vectors[0]) == 384
    assert requests[0][0].startswith("https://generativelanguage.googleapis.com/v1beta/")
    assert requests[0][0].endswith("?key=test-key")
    assert requests[0][1]["requests"][0]["taskType"] == "RETRIEVAL_QUERY"
    assert requests[0][1]["requests"][0]["outputDimensionality"] == 384
    assert embeddings.embedding_model_key() == "gemini:gemini-embedding-001:384"


def test_embedding_response_dimension_must_match_docs_configuration(monkeypatch):
    monkeypatch.setenv("DOCS_EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("DOCS_GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("DOCS_GEMINI_EMBEDDING_DIMENSIONS", "384")
    monkeypatch.setattr(
        embeddings,
        "_post_json",
        lambda *_args, **_kwargs: {"embeddings": [{"values": [0.1] * 768}]},
    )
    with pytest.raises(ValueError, match="DOCS_\\*_EMBEDDING_DIMENSIONS"):
        embeddings.embed_texts(["bad dimension"], task="document")


def test_gemini_provider_requires_existing_docs_key(monkeypatch):
    monkeypatch.setenv("DOCS_EMBEDDING_PROVIDER", "gemini")
    monkeypatch.delenv("DOCS_GEMINI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="DOCS_GEMINI_API_KEY"):
        embeddings.embed_texts(["text"], task="query")


def test_recommendation_dimensions_override_docs_vector_dimension(monkeypatch):
    monkeypatch.setenv("DOCS_EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("DOCS_GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("DOCS_GEMINI_EMBEDDING_DIMENSIONS", "384")
    requests = []

    def fake_post(_url, payload, _timeout, **_kwargs):
        requests.append(payload)
        dimensions = payload["requests"][0]["outputDimensionality"]
        return {
            "embeddings": [{"values": [0.25] * dimensions}]
            for _request in payload["requests"]
        }

    monkeypatch.setattr(embeddings, "_post_json", fake_post)
    for dimension in (384, 768):
        vectors = embeddings.embed_texts(
            ["Recommendation content"],
            task="document",
            dimensions=dimension,
        )

        assert len(vectors[0]) == dimension
        assert requests[-1]["requests"][0]["outputDimensionality"] == dimension
        assert (
            embeddings.embedding_model_key(dimensions=dimension)
            == f"gemini:gemini-embedding-001:{dimension}"
        )


@pytest.mark.parametrize("alias", ["google", "google_gemini", "google-gemini"])
def test_provider_normalizes_google_aliases_and_rejects_unknown(monkeypatch, alias):
    monkeypatch.setenv("DOCS_EMBEDDING_PROVIDER", alias)
    assert embeddings._provider() == "gemini"

    monkeypatch.setenv("DOCS_EMBEDDING_PROVIDER", "unsupported_provider")
    with pytest.raises(ValueError, match="Unsupported DOCS_EMBEDDING_PROVIDER"):
        embeddings._provider()


@pytest.mark.parametrize("bad_dim", [0, 2001, True, -5])
def test_embedding_dimensions_rejects_invalid_override_and_env(monkeypatch, bad_dim):
    with pytest.raises(ValueError, match="Embedding dimensions must be between 1 and 2000"):
        embeddings.embedding_dimensions(bad_dim)

    monkeypatch.setenv("DOCS_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("DOCS_OPENAI_EMBEDDING_DIMENSIONS", "0")
    with pytest.raises(
        ValueError, match="Configured embedding dimensions must be between 1 and 2000"
    ):
        embeddings.embedding_dimensions()


def test_embed_texts_validates_task_empty_and_blank_inputs():
    with pytest.raises(ValueError, match="Embedding task must be 'query' or 'document'"):
        embeddings.embed_texts(["hello"], task="invalid")

    assert embeddings.embed_texts([], task="query") == []

    with pytest.raises(ValueError, match="non-blank text"):
        embeddings.embed_texts(["valid", "   "], task="query")


def test_openai_embeddings_sort_by_index_and_support_e5_prefixes(monkeypatch):
    monkeypatch.setenv("DOCS_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("DOCS_OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("DOCS_OPENAI_EMBEDDING_MODEL", "intfloat/multilingual-e5-small")
    monkeypatch.setenv("DOCS_OPENAI_EMBEDDING_DIMENSIONS", "384")
    captured = []

    def fake_post(url, payload, timeout, **kwargs):
        captured.append((url, payload, timeout, kwargs))
        return {
            "data": [
                {"index": 1, "embedding": [0.5] * 384},
                {"index": 0, "embedding": [0.25] * 384},
            ]
        }

    monkeypatch.setattr(embeddings, "_post_json", fake_post)
    vectors = embeddings.embed_texts(["first", "second"], task="query")
    assert vectors[0][0] == 0.25
    assert vectors[1][0] == 0.5
    assert captured[0][1]["input"] == ["query: first", "query: second"]

    embeddings.embed_texts(["doc1", "doc2"], task="document")
    assert captured[1][1]["input"] == ["passage: doc1", "passage: doc2"]

    monkeypatch.setenv("DOCS_OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    embeddings.embed_texts(["plain1", "plain2"], task="query")
    assert captured[2][1]["input"] == ["plain1", "plain2"]


def test_openai_and_gemini_validation_errors(monkeypatch):
    monkeypatch.setenv("DOCS_EMBEDDING_PROVIDER", "openai")
    monkeypatch.delenv("DOCS_OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="DOCS_OPENAI_API_KEY"):
        embeddings.embed_texts(["hello"], task="query")

    monkeypatch.setenv("DOCS_OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("DOCS_OPENAI_EMBEDDING_DIMENSIONS", "384")

    for bad_response, match in [
        ({}, "missing data"),
        ({"data": ["not-a-dict"]}, "did not match input count"),
        ({"data": [{"index": True, "embedding": [0.1] * 384}]}, "invalid index"),
        ({"data": [{"index": 0, "embedding": "not-a-list"}]}, "did not match input count"),
        (
            {"data": [{"index": 0, "embedding": [True] * 384}]},
            "non-numeric value",
        ),
        (
            {"data": [{"index": 0, "embedding": [float("nan")] * 384}]},
            "non-finite vector value",
        ),
    ]:
        monkeypatch.setattr(
            embeddings, "_post_json", lambda *_a, _r=bad_response, **_k: _r
        )
        with pytest.raises(ValueError, match=match):
            embeddings.embed_texts(["hello"], task="query")

    monkeypatch.setenv("DOCS_EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("DOCS_GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("DOCS_GEMINI_EMBEDDING_DIMENSIONS", "384")
    for bad_gemini, match in [
        ({"embeddings": []}, "one vector per text"),
        ({"embeddings": [{"values": None}]}, "invalid vector"),
        ({"embeddings": [{"values": ["bad"] * 384}]}, "non-numeric value"),
    ]:
        monkeypatch.setattr(
            embeddings, "_post_json", lambda *_a, _r=bad_gemini, **_k: _r
        )
        with pytest.raises(ValueError, match=match):
            embeddings.embed_texts(["hello"], task="query")


def test_local_embeddings_and_local_embedder_cache(monkeypatch):
    import sys
    from types import SimpleNamespace

    monkeypatch.setenv("DOCS_EMBEDDING_PROVIDER", "local")
    monkeypatch.delenv("DOCS_LOCAL_EMBEDDING_MODEL", raising=False)
    monkeypatch.setenv("DOCS_LOCAL_EMBEDDING_DIMENSIONS", "384")
    monkeypatch.setenv("MODELS_DIR", "/tmp/models")
    monkeypatch.delenv("FASTEMBED_CACHE", raising=False)

    created = []

    class FakeTextEmbedding:
        def __init__(self, model_name, cache_dir):
            created.append((model_name, cache_dir))

        def embed(self, texts):
            return [[0.1] * 384 for _ in texts]

    embeddings._local_embedder.cache_clear()
    monkeypatch.setitem(
        sys.modules, "fastembed", SimpleNamespace(TextEmbedding=FakeTextEmbedding)
    )

    vectors = embeddings.embed_texts(["hello"], task="query")
    assert len(vectors) == 1
    assert created == [
        (embeddings.DEFAULT_LOCAL_EMBEDDING_MODEL, "/tmp/models/fastembed")
    ]
    assert (
        embeddings.embedding_model_key()
        == f"local:{embeddings.DEFAULT_LOCAL_EMBEDDING_MODEL}:384"
    )

    monkeypatch.setenv("DOCS_LOCAL_EMBEDDING_MODEL", "intfloat/multilingual-e5-base")
    monkeypatch.setenv("FASTEMBED_CACHE", "/tmp/custom-fastembed")
    embeddings._local_embedder.cache_clear()
    vectors_e5 = embeddings.embed_texts(["hello"], task="document")
    assert len(vectors_e5) == 1

    monkeypatch.setattr(
        embeddings, "_local_embeddings", lambda _texts, *, task: []
    )
    with pytest.raises(ValueError, match="one vector per text"):
        embeddings.embed_texts(["hello"], task="query")


def test_post_json_handles_success_and_http_url_json_errors(monkeypatch):
    import io
    from urllib.error import HTTPError, URLError

    class FakeResponse:
        def __init__(self, body: bytes):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self) -> bytes:
            return self._body

    monkeypatch.setattr(
        embeddings, "urlopen", lambda *_a, **_k: FakeResponse(b'{"ok": true}')
    )
    assert (
        embeddings._post_json("https://example.com", {"a": 1}, 5.0, provider="Test")
        == {"ok": True}
    )

    monkeypatch.setattr(
        embeddings, "urlopen", lambda *_a, **_k: FakeResponse(b"[1, 2, 3]")
    )
    with pytest.raises(ValueError, match="must be a JSON object"):
        embeddings._post_json("https://example.com", {"a": 1}, 5.0, provider="Test")

    def raise_http(*_a, **_k):
        raise HTTPError("https://example.com", 502, "Bad Gateway", None, io.BytesIO(b""))

    monkeypatch.setattr(embeddings, "urlopen", raise_http)
    with pytest.raises(RuntimeError, match="HTTP 502"):
        embeddings._post_json("https://example.com", {"a": 1}, 5.0, provider="Test")

    def raise_url(*_a, **_k):
        raise URLError("network down")

    monkeypatch.setattr(embeddings, "urlopen", raise_url)
    with pytest.raises(RuntimeError, match="network down"):
        embeddings._post_json("https://example.com", {"a": 1}, 5.0, provider="Test")

