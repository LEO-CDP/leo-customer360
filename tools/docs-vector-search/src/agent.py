"""Graph-RAG agent: retrieve → graph-expand → answer with cited sources.

  python -m src.agent "How does identity resolution merge profiles?"
  python -m src.agent                     # interactive REPL
  python -m src.agent --top-k 8 --hops 1 --show-context "..."
"""
from __future__ import annotations

import argparse
import sys

from .config import HOPS, TOP_K
from .providers import chat
from .retriever import Index, build_context

ANSWER_SYSTEM = (
    "You answer questions using ONLY the provided documentation excerpts from the "
    "LEO Customer 360 repository. Ground every claim in those excerpts and cite the "
    "document titles you used, like [Title]. If the docs do not contain the answer, "
    "say so plainly rather than inventing one."
)


def query(question: str, index: Index, top_k: int = TOP_K, hops: int = HOPS) -> dict:
    ranked = index.search(question, top_k)
    selected = index.expand([d for d, _ in ranked], hops)
    context = build_context(selected)
    answer = chat(ANSWER_SYSTEM, f"Documentation:\n\n{context}\n\nQuestion: {question}")
    return {
        "answer": answer,
        "sources": [
            {"path": d.path, "title": d.title, "score": round(s, 4)} for d, s in ranked
        ],
        "used_docs": [d.path for d in selected],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Ask the docs corpus a question.")
    ap.add_argument("question", nargs="*", help="the question (omit for a REPL)")
    ap.add_argument("--top-k", type=int, default=TOP_K)
    ap.add_argument("--hops", type=int, default=HOPS)
    ap.add_argument("--show-context", action="store_true", help="print the retrieved context")
    args = ap.parse_args()
    index = Index.load()

    def ask(q: str) -> None:
        if args.show_context:
            print(build_context(index.expand([d for d, _ in index.search(q, args.top_k)], args.hops)))
            print("\n" + "=" * 60)
        result = query(q, index, args.top_k, args.hops)
        print("\n" + result["answer"] + "\n\nSources:")
        for s in result["sources"]:
            print(f"  [{s['score']:.3f}] {s['title']}  ({s['path']})")

    if args.question:
        ask(" ".join(args.question))
        return
    print("Docs RAG REPL — type a question, Ctrl-D to exit.")
    for line in sys.stdin:
        if line.strip():
            ask(line.strip())


if __name__ == "__main__":
    main()
