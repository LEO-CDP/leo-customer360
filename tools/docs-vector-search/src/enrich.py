"""CLI wrapper for building the document vector and keyword indexes."""
from __future__ import annotations

import argparse

from .indexing import EmptyCorpusError, build


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the docs vector and FTS indexes in pgvector.")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--batch", type=int, default=None, help="embedding batch size (default: DOCS_ENRICH_BATCH_SIZE env or 64)")
    args = ap.parse_args()
    try:
        build(dry_run=args.dry_run, batch=args.batch)
    except EmptyCorpusError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
