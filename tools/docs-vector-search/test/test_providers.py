from __future__ import annotations

from src import providers


def test_gemini_embeddings_use_retrieval_task_and_validate_dimensions(monkeypatch):
    calls = []

    def fake_request(path, payload, **kwargs):
        calls.append((path, payload))
        return {"embeddings": [{"values": [0.1, 0.2, 0.3]}, {"values": [0.4, 0.5, 0.6]}]}

    monkeypatch.setattr(providers, "_gemini_request", fake_request)
    monkeypatch.setattr(providers, "GEMINI_EMBEDDING_MODEL", "gemini-test-embedding")
    monkeypatch.setattr(providers, "GEMINI_EMBEDDING_DIMENSIONS", 3)
    monkeypatch.setattr(providers, "EMBED_DIM", 3)

    vectors = providers._gemini_embeddings(["question", "document"], task="query")

    assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert calls[0][0] == "models:batchEmbedContents"
    assert calls[0][1]["requests"][0]["taskType"] == "RETRIEVAL_QUERY"
    assert calls[0][1]["requests"][0]["model"] == "models/gemini-test-embedding"


def test_gemini_generation_maps_system_instruction_and_answer(monkeypatch):
    calls = []

    def fake_request(path, payload):
        calls.append((path, payload))
        return {"candidates": [{"content": {"parts": [{"text": " grounded answer "}]}}]}

    monkeypatch.setattr(providers, "_gemini_request", fake_request)
    monkeypatch.setattr(providers, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(providers, "GEMINI_LLM_MODEL", "gemini-test-model")
    monkeypatch.setattr(providers, "DOCS_HOSTED_LLM_MAX_OUTPUT_TOKENS", 17)

    answer = providers.generate("system rules", "user question")

    assert answer == "grounded answer"
    assert calls[0][0] == "models/gemini-test-model:generateContent"
    assert calls[0][1]["systemInstruction"]["parts"][0]["text"] == "system rules"
    assert calls[0][1]["contents"][0]["parts"][0]["text"] == "user question"
    assert calls[0][1]["generationConfig"]["maxOutputTokens"] == 17


def test_embedding_provider_dispatches_to_gemini_without_loading_local_model(monkeypatch):
    monkeypatch.setattr(providers, "EMBED_PROVIDER", "gemini")
    monkeypatch.setattr(
        providers, "_gemini_embeddings", lambda texts, task: [[1.0, 2.0] for _ in texts]
    )

    assert providers.embed(["text"], task="document") == [[1.0, 2.0]]


def test_openai_rerank_scores_all_passages_in_one_request(monkeypatch):
    calls = []

    def fake_request(path, payload, **kwargs):
        calls.append((path, payload))
        return {"choices": [{"message": {"content": '{"scores": [91, 4]} '}}]}

    monkeypatch.setattr(providers, "_openai_request", fake_request)
    monkeypatch.setattr(providers, "OPENAI_RERANK_MODEL", "gpt-4o-mini")

    scores = providers._openai_rerank("What is CIR?", ["CIR definition", "deployment notes"])

    assert scores == [91.0, 4.0]
    assert calls[0][0] == "chat/completions"
    assert calls[0][1]["model"] == "gpt-4o-mini"
    assert calls[0][1]["response_format"] == {"type": "json_object"}
    assert "[0]\nCIR definition" in calls[0][1]["messages"][1]["content"]
    assert "[1]\ndeployment notes" in calls[0][1]["messages"][1]["content"]


def test_openai_rerank_accepts_content_parts(monkeypatch):
    def fake_request(path, payload, **kwargs):
        return {
            "choices": [
                {
                    "message": {
                        "content": [{"type": "text", "text": '{"scores": [80]}'}]
                    }
                }
            ]
        }

    monkeypatch.setattr(providers, "_openai_request", fake_request)

    assert providers._openai_rerank("query", ["passage"]) == [80.0]


def test_openai_rerank_failure_preserves_vector_order_by_default(monkeypatch):
    monkeypatch.setattr(providers, "DOCS_RERANK_PROVIDER", "openai")
    monkeypatch.setattr(providers, "DOCS_RERANK_OPENAI_FALLBACK", "vector")
    monkeypatch.setattr(
        providers, "_openai_rerank", lambda query, passages: (_ for _ in ()).throw(RuntimeError("offline"))
    )

    assert providers.rerank("query", ["first", "second"]) == [0.0, 0.0]


def test_openai_generation_retries_empty_completion_and_accepts_content_parts(monkeypatch):
    calls = []
    responses = [
        {
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
            "usage": {"completion_tokens": 256},
        },
        {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "Grounded "},
                            {"type": "text", "text": "answer."},
                        ]
                    },
                    "finish_reason": "stop",
                }
            ]
        },
    ]

    def fake_request(path, payload, **kwargs):
        calls.append((path, payload))
        return responses.pop(0)

    monkeypatch.setattr(providers, "_openai_request", fake_request)
    monkeypatch.setattr(providers, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(providers, "OPENAI_LLM_MODEL", "gpt-5.6-luna")
    monkeypatch.setattr(providers, "DOCS_HOSTED_LLM_MAX_OUTPUT_TOKENS", 256)

    assert providers.generate("rules", "question") == "Grounded answer."
    assert calls[0][1]["max_completion_tokens"] == 1024
    assert calls[1][1]["max_completion_tokens"] == 2048


def test_openai_generation_retries_a_truncated_non_empty_completion(monkeypatch):
    calls = []
    responses = [
        {
            "choices": [
                {
                    "message": {"content": "The first part of the answer"},
                    "finish_reason": "length",
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {"content": "The complete answer."},
                    "finish_reason": "stop",
                }
            ]
        },
    ]

    def fake_request(path, payload, **kwargs):
        calls.append((path, payload))
        return responses.pop(0)

    monkeypatch.setattr(providers, "_openai_request", fake_request)
    monkeypatch.setattr(providers, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(providers, "OPENAI_LLM_MODEL", "gpt-5.6-luna")
    monkeypatch.setattr(providers, "DOCS_HOSTED_LLM_MAX_OUTPUT_TOKENS", 256)

    assert providers.generate("rules", "question") == "The complete answer."
    assert calls[0][1]["max_completion_tokens"] == 1024
    assert calls[1][1]["max_completion_tokens"] == 2048


def test_openai_generation_never_returns_blank_answer(monkeypatch):
    def fake_request(path, payload, **kwargs):
        return {
            "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 0},
        }

    monkeypatch.setattr(providers, "_openai_request", fake_request)
    monkeypatch.setattr(providers, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(providers, "OPENAI_LLM_MODEL", "gpt-5.6-luna")

    try:
        providers.generate("rules", "question")
    except RuntimeError as exc:
        assert "no visible answer" in str(exc)
    else:
        raise AssertionError("blank generation was returned to the caller")


def test_local_generation_uses_the_safe_qwen_output_budget(monkeypatch):
    calls = []

    class FakeModel:
        def create_chat_completion(self, **kwargs):
            calls.append(kwargs)
            return {"choices": [{"message": {"content": "Local answer."}}]}

    monkeypatch.setattr(providers, "LLM_PROVIDER", "local")
    monkeypatch.setattr(providers, "DOCS_LOCAL_LLM_MAX_OUTPUT_TOKENS", 512)
    monkeypatch.setattr(providers, "_llm", lambda: FakeModel())

    assert providers.generate("rules", "question") == "Local answer."
    assert calls[0]["max_tokens"] == 512


def test_local_generation_retries_a_truncated_completion(monkeypatch):
    calls = []

    class FakeModel:
        def create_chat_completion(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return {
                    "choices": [
                        {
                            "message": {"content": "The first part"},
                            "finish_reason": "length",
                        }
                    ]
                }
            return {
                "choices": [
                    {
                        "message": {"content": "The complete local answer."},
                        "finish_reason": "stop",
                    }
                ]
            }

    monkeypatch.setattr(providers, "LLM_PROVIDER", "local")
    monkeypatch.setattr(providers, "DOCS_LOCAL_LLM_MAX_OUTPUT_TOKENS", 512)
    monkeypatch.setattr(providers, "LOCAL_LLM_CONTEXT_TOKENS", 2048)
    monkeypatch.setattr(providers, "_llm", lambda: FakeModel())

    assert providers.generate("rules", "question") == "The complete local answer."
    assert calls[0]["max_tokens"] == 512
    assert calls[1]["max_tokens"] == 1024