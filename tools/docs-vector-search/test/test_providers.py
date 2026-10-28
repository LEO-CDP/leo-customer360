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

    def fake_request(path, payload, **kwargs):
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


def test_openai_rerank_asks_for_the_top_candidates_and_scores_them_by_rank(monkeypatch):
    calls = []

    def fake_request(path, payload, **kwargs):
        calls.append((path, payload))
        return {"choices": [{"message": {"content": '{"top": [2, 0]} '}}]}

    monkeypatch.setattr(providers, "_openai_request", fake_request)
    monkeypatch.setattr(providers, "OPENAI_RERANK_MODEL", "gpt-4o-mini")

    scores = providers._openai_rerank("What is CIR?", ["a", "b", "CIR definition"])

    assert scores == [1.0, 0.0, 2.0]  # best first: candidate 2, then 0; 1 unranked
    assert calls[0][0] == "chat/completions"
    assert calls[0][1]["response_format"]["type"] == "json_schema"
    assert calls[0][1]["max_tokens"] == 400
    assert "[2]\nCIR definition" in calls[0][1]["messages"][1]["content"]


def test_openai_rerank_gives_reasoning_models_room_to_think(monkeypatch):
    sent = {}

    def fake_request(path, payload, **kwargs):
        sent.update(payload)
        return {"choices": [{"message": {"content": [{"type": "text", "text": '{"top": [0]}'}]}}]}

    monkeypatch.setattr(providers, "_openai_request", fake_request)
    monkeypatch.setattr(providers, "OPENAI_RERANK_MODEL", "openai/gpt-5-nano")

    assert providers._openai_rerank("query", ["passage"]) == [1.0]
    assert sent["max_completion_tokens"] == 2500 and "max_tokens" not in sent


def test_openai_rerank_rejects_unusable_rankings(monkeypatch):
    for bad in ('{"top": []}', '{"top": [5]}', '{"top": ["0"]}', '{"top": [true]}', "not json", "{}"):
        monkeypatch.setattr(
            providers, "_openai_request",
            lambda path, payload, bad=bad, **kw: {"choices": [{"message": {"content": bad}}]},
        )
        try:
            providers._openai_rerank("query", ["only one"])
        except RuntimeError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_openai_rerank_ignores_repeated_candidate_numbers(monkeypatch):
    monkeypatch.setattr(
        providers, "_openai_request",
        lambda path, payload, **kw: {"choices": [{"message": {"content": '{"top": [1, 1, 0]}'}}]},
    )
    assert providers._openai_rerank("q", ["a", "b"]) == [1.0, 2.0]


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


def test_local_generation_honours_max_tokens(monkeypatch):
    calls = []

    class FakeModel:
        def create_chat_completion(self, **kwargs):
            calls.append(kwargs)
            return {"choices": [{"message": {"content": "Local answer."}}]}

    monkeypatch.setattr(providers, "LLM_PROVIDER", "local")
    monkeypatch.setattr(providers, "DOCS_LOCAL_LLM_MAX_OUTPUT_TOKENS", 512)
    monkeypatch.setattr(providers, "_llm", lambda: FakeModel())

    assert providers.generate("rules", "question", max_tokens=200) == "Local answer."
    assert calls[0]["max_tokens"] == 200


def test_local_generation_retry_budget_uses_passed_max_tokens(monkeypatch):
    calls = []
    responses = [
        {"choices": [{"message": {"content": "The first part"}, "finish_reason": "length"}]},
        {"choices": [{"message": {"content": "The complete local answer."}}]},
    ]

    class FakeModel:
        def create_chat_completion(self, **kwargs):
            calls.append(kwargs)
            return responses.pop(0)

    monkeypatch.setattr(providers, "LLM_PROVIDER", "local")
    monkeypatch.setattr(providers, "DOCS_LOCAL_LLM_MAX_OUTPUT_TOKENS", 512)
    monkeypatch.setattr(providers, "LOCAL_LLM_CONTEXT_TOKENS", 2048)
    monkeypatch.setattr(providers, "_llm", lambda: FakeModel())

    assert providers.generate("rules", "question", max_tokens=200) == "The complete local answer."
    assert calls[0]["max_tokens"] == 200
    assert calls[1]["max_tokens"] == providers._retry_token_budget(
        200, context_limit=2048
    )


def test_local_generation_retry_budget_is_capped_by_context(monkeypatch):
    calls = []
    responses = [
        {"choices": [{"message": {"content": "The first part"}, "finish_reason": "length"}]},
        {"choices": [{"message": {"content": "The complete local answer."}}]},
    ]

    class FakeModel:
        def create_chat_completion(self, **kwargs):
            calls.append(kwargs)
            return responses.pop(0)

    monkeypatch.setattr(providers, "LLM_PROVIDER", "local")
    monkeypatch.setattr(providers, "DOCS_LOCAL_LLM_MAX_OUTPUT_TOKENS", 512)
    monkeypatch.setattr(providers, "LOCAL_LLM_CONTEXT_TOKENS", 900)
    monkeypatch.setattr(providers, "_llm", lambda: FakeModel())

    assert providers.generate("rules", "question", max_tokens=200) == "The complete local answer."
    assert calls[1]["max_tokens"] == 899


def test_local_generation_accepts_timeout_without_raising(monkeypatch):
    calls = []

    class FakeModel:
        def create_chat_completion(self, **kwargs):
            calls.append(kwargs)
            return {"choices": [{"message": {"content": "Local answer."}}]}

    monkeypatch.setattr(providers, "LLM_PROVIDER", "local")
    monkeypatch.setattr(providers, "DOCS_LOCAL_LLM_MAX_OUTPUT_TOKENS", 512)
    monkeypatch.setattr(providers, "_llm", lambda: FakeModel())

    assert providers.generate("rules", "question", timeout=6) == "Local answer."
    assert calls[0]["max_tokens"] == 512


def test_openai_generation_sends_the_json_schema_when_given(monkeypatch):
    sent = {}

    def fake_request(path, payload, **kwargs):
        sent.update(payload)
        return {"choices": [{"message": {"content": '{"status":"answered"}'}, "finish_reason": "stop"}]}

    schema = {"type": "object", "properties": {"status": {"type": "string"}}, "required": ["status"]}
    monkeypatch.setattr(providers, "_openai_request", fake_request)
    monkeypatch.setattr(providers, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(providers, "OPENAI_LLM_MODEL", "gpt-5.6-luna")

    assert providers.generate("sys", "user", schema) == '{"status":"answered"}'
    assert sent["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "support_answer", "strict": True, "schema": schema},
    }

    sent.clear()
    providers.generate("sys", "user")
    assert "response_format" not in sent


def test_gemini_generation_requests_json_when_given_a_schema(monkeypatch):
    sent = {}

    def fake_request(path, payload, **kwargs):
        sent.update(payload)
        return {"candidates": [{"content": {"parts": [{"text": "{}"}]}, "finishReason": "STOP"}]}

    schema = {"type": "object"}
    monkeypatch.setattr(providers, "_gemini_request", fake_request)
    monkeypatch.setattr(providers, "LLM_PROVIDER", "gemini")

    providers.generate("sys", "user", schema)

    config = sent["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseJsonSchema"] == schema


def test_gemini_generation_honours_timeout_and_max_tokens(monkeypatch):
    calls = []

    def fake_request(path, payload, **kwargs):
        calls.append((path, payload, kwargs))
        return {
            "candidates": [
                {"content": {"parts": [{"text": "rewritten"}]}, "finishReason": "STOP"}
            ]
        }

    monkeypatch.setattr(providers, "_gemini_request", fake_request)
    monkeypatch.setattr(providers, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(providers, "DOCS_HOSTED_LLM_MAX_OUTPUT_TOKENS", 4096)

    assert providers.generate("sys", "user", timeout=6, max_tokens=200) == "rewritten"
    assert calls[0][1]["generationConfig"]["maxOutputTokens"] == 200
    assert calls[0][2]["timeout"] == 6


def test_gemini_generation_retry_honours_timeout_and_token_budget(monkeypatch):
    calls = []
    responses = [
        {"candidates": [{"content": {"parts": [{"text": ""}]}, "finishReason": "MAX_TOKENS"}]},
        {"candidates": [{"content": {"parts": [{"text": "complete"}]}, "finishReason": "STOP"}]},
    ]

    def fake_request(path, payload, **kwargs):
        calls.append((path, payload, kwargs))
        return responses.pop(0)

    monkeypatch.setattr(providers, "_gemini_request", fake_request)
    monkeypatch.setattr(providers, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(providers, "DOCS_HOSTED_LLM_MAX_OUTPUT_TOKENS", 4096)

    assert providers.generate("sys", "user", timeout=6, max_tokens=200) == "complete"
    assert calls[0][1]["generationConfig"]["maxOutputTokens"] == 200
    assert calls[1][1]["generationConfig"]["maxOutputTokens"] == providers._retry_token_budget(200)
    assert calls[0][2]["timeout"] == 6
    assert calls[1][2]["timeout"] == 6


def test_gemini_generation_defaults_to_configured_budget_and_timeout(monkeypatch):
    calls = []

    def fake_request(path, payload, **kwargs):
        calls.append((path, payload, kwargs))
        return {
            "candidates": [
                {"content": {"parts": [{"text": "answer"}]}, "finishReason": "STOP"}
            ]
        }

    monkeypatch.setattr(providers, "_gemini_request", fake_request)
    monkeypatch.setattr(providers, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(providers, "DOCS_HOSTED_LLM_MAX_OUTPUT_TOKENS", 777)

    assert providers.generate("sys", "user") == "answer"
    assert calls[0][1]["generationConfig"]["maxOutputTokens"] == 777
    assert calls[0][2]["timeout"] is None


def test_gemini_request_forwards_timeout_and_falls_back_to_default(monkeypatch):
    seen = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(request, timeout=None):
        seen["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(providers, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(providers, "urlopen", fake_urlopen)

    assert providers._gemini_request("models/x:generateContent", {}, timeout=6) == {"ok": True}
    assert seen["timeout"] == 6

    providers._gemini_request("models/x:generateContent", {})
    assert seen["timeout"] == providers.OPENAI_REQUEST_TIMEOUT_SECONDS


def test_gemini_request_read_timeout_becomes_runtime_error(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise TimeoutError("read timed out")

    monkeypatch.setattr(providers, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(providers, "urlopen", fake_urlopen)

    try:
        providers._gemini_request("models/x:generateContent", {})
    except RuntimeError as exc:
        assert "Gemini request" in str(exc)
    else:
        raise AssertionError("timeout did not become a RuntimeError")


def test_rerank_failures_are_counted_so_health_can_show_them(monkeypatch):
    monkeypatch.setattr(providers, "DOCS_RERANK_PROVIDER", "openai")
    monkeypatch.setattr(providers, "DOCS_RERANK_OPENAI_FALLBACK", "vector")
    monkeypatch.setattr(providers, "_rerank_stats", {"ok": 0, "failed": 0, "last_error": None})
    outcomes = iter([[2.0, 1.0], RuntimeError("offline")])

    def fake_rerank(query, passages):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(providers, "_openai_rerank", fake_rerank)

    providers.rerank("q", ["a", "b"])
    providers.rerank("q", ["a", "b"])

    assert providers.rerank_stats() == {"ok": 1, "failed": 1, "last_error": "offline"}
