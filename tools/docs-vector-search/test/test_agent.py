from __future__ import annotations

from src.agent import ANSWER_SYSTEM, RagAgent


def _hit(chunk_id: str, text: str) -> dict:
    return {
        "id": chunk_id,
        "path": "guide.md",
        "title": "Guide",
        "heading": "Identity",
        "text": text,
        "score": 0.5,
    }


def test_rag_agent_injects_hybrid_retrieval_and_generation_dependencies():
    calls = []

    class FakeRepository:
        def retrieve(self, question, query_vector, limit, keyword_limit):
            calls.append((question, query_vector, limit, keyword_limit))
            return [_hit("a", "CIR is Customer Identity Resolution.")]

    def fake_embedder(texts, *, task):
        assert task == "query"
        return [[1.0, 2.0]]

    def fake_reranker(question, passages):
        assert question == "CIR là gì?"
        assert passages == ["CIR is Customer Identity Resolution."]
        return [9.0]

    def fake_generator(system_prompt, user_message):
        assert "CIR là gì?" in user_message
        return "CIR is Customer Identity Resolution."

    agent = RagAgent(
        repository=FakeRepository(),
        embedder=fake_embedder,
        reranker=fake_reranker,
        generator=fake_generator,
        keyword_top_n=80,
    )

    result = agent.answer("CIR là gì?", top_n=4, top_k=2)

    assert calls == [("CIR là gì?", [1.0, 2.0], 4, 80)]
    assert result["answer"].startswith("CIR is")
    assert result["contexts"] == ["CIR is Customer Identity Resolution."]
    assert result["sources"][0]["path"] == "guide.md"


def test_answer_prompt_requires_bilingual_grounded_non_empty_output():
    assert "same language as the question" in ANSWER_SYSTEM
    assert "Do not return JSON, XML, analysis," in ANSWER_SYSTEM
    assert "or an empty response." in ANSWER_SYSTEM
    assert "I don't know — that isn't in the documentation." in ANSWER_SYSTEM


def test_rag_agent_rejects_empty_generator_output():
    class FakeRepository:
        def retrieve(self, question, query_vector, limit, keyword_limit):
            return [_hit("a", "Grounding evidence.")]

    agent = RagAgent(
        repository=FakeRepository(),
        embedder=lambda texts, *, task: [[1.0]],
        reranker=lambda question, passages: [1.0],
        generator=lambda system_prompt, user_message: "  ",
    )

    try:
        agent.answer("What is this?", top_n=1, top_k=1)
    except RuntimeError as exc:
        assert "empty response" in str(exc)
    else:
        raise AssertionError("empty generated answer was accepted")