"""CLI wrapper for building the document vector and keyword indexes."""
from __future__ import annotations

import argparse

from .indexing import EmptyCorpusError, build


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the docs vector and FTS indexes in pgvector.")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    try:
        build(dry_run=ap.parse_args().dry_run)
    except EmptyCorpusError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
