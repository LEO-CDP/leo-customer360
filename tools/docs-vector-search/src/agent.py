"""RAG agent: embed query → pgvector top-N → rerank → grounded answer + sources.

  python -m src.agent "How does identity resolution merge two profiles?"
  python -m src.agent                    # interactive REPL
"""
from __future__ import annotations

import sys

from . import store
from .config import CONTEXT_CHAR_BUDGET, RERANK_ENABLED, RERANK_TOP_K, RETRIEVE_TOP_N
from .providers import embed, generate, rerank

ANSWER_SYSTEM = (
    "You are a documentation assistant for LEO Customer 360. Use ONLY the context below — "
    "never use outside or prior knowledge, even for general-knowledge questions. If the "
    'answer is not clearly in the context, reply EXACTLY: "I don\'t know — that isn\'t in '
    'the documentation." and nothing else. When the context does answer, be concise and '
    "cite the source titles you used in [brackets]."
)


def _build_context(hits: list[dict], budget: int = CONTEXT_CHAR_BUDGET) -> str:
    blocks, used = [], 0
    for h in hits:
        block = f"## {h['title']} — {h['heading']}\n{h['text']}"
        if used + len(block) > budget and blocks:
            break
        blocks.append(block)
        used += len(block)
    return "\n\n---\n\n".join(blocks)


def retrieve(question: str, conn, top_n: int = RETRIEVE_TOP_N) -> list[dict]:
    """Embed the query → pgvector top-N → rerank (if enabled). Shared by /ask and /search."""
    hits = store.search(conn, embed([question], task="query")[0], top_n)
    if RERANK_ENABLED and hits:
        for h, s in zip(hits, rerank(question, [h["text"] for h in hits])):
            h["rerank"] = s
        hits.sort(key=lambda h: h["rerank"], reverse=True)
    return hits


def query(question: str, conn, top_n: int = RETRIEVE_TOP_N, top_k: int = RERANK_TOP_K) -> dict:
    hits = retrieve(question, conn, top_n)[:top_k]
    answer = generate(ANSWER_SYSTEM, f"Context:\n\n{_build_context(hits)}\n\nQuestion: {question}")
    return {
        "answer": answer,
        # The chunk texts the generator actually saw — exposed so evaluation (RAGAS
        # faithfulness / context metrics) scores the same context the answer used.
        "contexts": [h["text"] for h in hits],
        "sources": [
            {"path": h["path"], "title": h["title"], "heading": h["heading"]} for h in hits
        ],
    }


def main() -> None:
    conn = store.connect()

    def ask(q: str) -> None:
        result = query(q, conn)
        print("\n" + result["answer"] + "\n\nSources:")
        for s in result["sources"]:
            print(f"  - {s['title']} — {s['heading']}  ({s['path']})")

    args = sys.argv[1:]
    if args:
        ask(" ".join(args))
        return
    print("Docs RAG REPL — type a question, Ctrl-D to exit.")
    for line in sys.stdin:
        if line.strip():
            ask(line.strip())


if __name__ == "__main__":
    main()
