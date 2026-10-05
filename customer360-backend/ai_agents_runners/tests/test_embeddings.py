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
