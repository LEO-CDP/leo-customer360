"""Build / refresh the embeddings + graph index (data/embeddings.json).

Idempotent: a content hash over the human text means unchanged docs reuse their
cached vector + graph and skip the embedder and the LLM entirely.

  python -m src.enrich                # embed + extract graph + write
  python -m src.enrich --no-extract   # embeddings only (no LLM calls)
  python -m src.enrich --dry-run      # preview what would change, write nothing
"""
from __future__ import annotations

import argparse
import json

from .config import EMBED_MODEL, INDEX_PATH
from .corpus import hydrate, load_docs, read_index_cache
from .providers import chat, embed

EXTRACT_SYSTEM = (
    "Extract the key domain entities and typed relations from this documentation. "
    "Entities are concrete nouns a reader would search for (systems, tables, "
    "components, concepts). Each relation connects two entities with a short verb "
    "phrase. Be precise; never invent facts that are not in the text."
)
GRAPH_SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {"type": "array", "items": {"type": "string"}},
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "relation": {"type": "string"},
                    "target": {"type": "string"},
                },
                "required": ["source", "relation", "target"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["entities", "relations"],
    "additionalProperties": False,
}


def build(*, dry_run: bool = False, extract: bool = True) -> None:
    docs = load_docs()
    fresh = hydrate(docs, read_index_cache())  # attaches cached vectors; returns misses

    print(f"{len(docs)} docs — {len(docs) - len(fresh)} unchanged, {len(fresh)} to (re)embed")
    if dry_run:
        for d in fresh:
            print(f"  would embed: {d.path}")
        return

    if fresh:
        for d, vector in zip(fresh, embed([d.body for d in fresh], task="document")):
            d.vector = vector
        if extract:
            for d in fresh:
                try:
                    graph = chat(EXTRACT_SYSTEM, d.body[:12000], schema=GRAPH_SCHEMA)
                    d.entities = graph.get("entities", [])
                    d.relations = [
                        f"{r['source']} — {r['relation']} — {r['target']}"
                        for r in graph.get("relations", [])
                    ]
                    print(f"  graph: {d.path} ({len(d.entities)} entities)")
                except Exception as e:  # noqa: BLE001 — one bad doc shouldn't abort the run
                    print(f"  WARN extract failed for {d.path}: {e}")

    out = {
        "model": EMBED_MODEL,
        "dim": len(docs[0].vector) if docs and docs[0].vector else 0,
        "docs": {
            d.path: {
                "hash": d.content_hash,
                "title": d.title,
                "vector": d.vector,
                "entities": d.entities,
                "relations": d.relations,
                "links": d.links,
            }
            for d in docs
        },
    }
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(out), encoding="utf-8")
    print(f"Wrote {INDEX_PATH} — {len(docs)} docs, dim={out['dim']}, model={EMBED_MODEL}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the docs vector + graph index.")
    ap.add_argument("--dry-run", action="store_true", help="preview only, write nothing")
    ap.add_argument("--no-extract", action="store_true", help="embeddings only, no LLM")
    args = ap.parse_args()
    build(dry_run=args.dry_run, extract=not args.no_extract)


if __name__ == "__main__":
    main()
