"""Build / refresh the index: chunk docs → embed (e5) → upsert into pgvector.

Idempotent: a content hash per chunk means unchanged chunks skip the embedder;
chunks that vanished from the corpus are pruned.

  python -m src.enrich              # embed changed chunks + upsert + prune
  python -m src.enrich --dry-run    # report what would change, write nothing
"""
from __future__ import annotations

import argparse

from . import store
from .config import PG_SCHEMA
from .corpus import load_chunks
from .providers import embed


def build(*, dry_run: bool = False, batch: int = 64) -> None:
    chunks = load_chunks()
    if not chunks:
        # An empty chunk set almost always means a mis-mounted or empty CORPUS_DIR, not a
        # deliberate wipe. Continuing would prune to an empty keep-set and DELETE the whole
        # index, so refuse and leave it intact (non-zero exit so CI/deploy notices).
        raise SystemExit(
            "Refusing to build: no chunks found under CORPUS_DIR — check it is mounted and "
            "non-empty. The existing index was left untouched."
        )
    # Context-manage the connection so it is closed on every path (dry-run, error, success).
    with store.connect() as conn:
        store.init_schema(conn)
        existing = store.existing_hashes(conn)
        fresh = [c for c in chunks if existing.get(c.id) != c.content_hash]

        print(f"{len(chunks)} chunks — {len(chunks) - len(fresh)} unchanged, {len(fresh)} to (re)embed")
        if dry_run:
            return

        for i in range(0, len(fresh), batch):
            part = fresh[i : i + batch]
            vectors = embed([c.text for c in part], task="document")
            store.upsert(
                conn,
                [
                    (c.id, c.path, c.title, c.heading, c.ordinal, c.content_hash, c.text, v)
                    for c, v in zip(part, vectors)
                ],
            )
            print(f"  embedded {min(i + batch, len(fresh))}/{len(fresh)}")

        pruned = store.prune(conn, [c.id for c in chunks])
        print(f"Done — {store.count(conn)} chunks in {PG_SCHEMA}.doc_chunks (pruned {pruned}).")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the docs vector index in pgvector.")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    build(dry_run=ap.parse_args().dry_run)


if __name__ == "__main__":
    main()
