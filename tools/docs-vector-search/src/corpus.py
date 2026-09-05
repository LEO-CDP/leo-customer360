"""Corpus loading: discover markdown, extract human text, hash, and resolve links.

Shared by enrich (build the index) and retriever (read fresh titles/bodies at
query time), so the Doc model and loading live here once.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

from .config import CORPUS_DIR, EMBED_MODEL, INDEX_PATH, REPO_ROOT

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")
MDLINK_RE = re.compile(r"\]\(([^)]+\.md)[^)]*\)")
AIGRAPH_RE = re.compile(r"<!--\s*ai-graph:start\s*-->.*?<!--\s*ai-graph:end\s*-->", re.S)
H1_RE = re.compile(r"^#\s+(.+)$", re.M)


@dataclass
class Doc:
    path: str  # repo-relative posix path, e.g. "docs/CIR-improvement.md"
    title: str
    body: str  # human text (frontmatter + any ai-graph block stripped)
    content_hash: str
    links: list[str] = field(default_factory=list)  # resolved repo-relative doc paths
    vector: list[float] | None = None  # filled from the index / enrichment
    entities: list[str] = field(default_factory=list)
    relations: list[str] = field(default_factory=list)


def _title(post, path: Path) -> str:
    if post.get("title"):
        return str(post["title"])
    m = H1_RE.search(post.content)
    return m.group(1).strip() if m else path.stem


def load_docs() -> list[Doc]:
    """Load every *.md under CORPUS_DIR as a Doc, with links resolved between them."""
    docs: dict[str, Doc] = {}
    contents: dict[str, str] = {}
    stem_index: dict[str, str] = {}

    for path in sorted(CORPUS_DIR.rglob("*.md")):
        post = frontmatter.loads(path.read_text(encoding="utf-8"))
        rel = path.relative_to(REPO_ROOT).as_posix()
        body = AIGRAPH_RE.sub("", post.content).strip()
        doc = Doc(
            path=rel,
            title=_title(post, path),
            body=body,
            content_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        )
        docs[rel] = doc
        contents[rel] = post.content
        stem_index.setdefault(path.stem.lower(), rel)
        stem_index.setdefault(doc.title.lower(), rel)

    known = set(docs)
    for rel, doc in docs.items():
        doc_dir = (REPO_ROOT / rel).parent
        targets: set[str] = set()
        for m in MDLINK_RE.finditer(contents[rel]):  # relative markdown links to .md
            try:
                trel = (doc_dir / m.group(1)).resolve().relative_to(REPO_ROOT).as_posix()
            except ValueError:
                continue
            if trel in known:
                targets.add(trel)
        for m in WIKILINK_RE.finditer(contents[rel]):  # [[wikilinks]] by stem/title
            key = m.group(1).strip().lower()
            if key in stem_index:
                targets.add(stem_index[key])
        doc.links = sorted(targets - {rel})

    return list(docs.values())


def read_index_cache() -> dict:
    """Per-doc {hash, vector, entities, relations} keyed by path — but only if the
    index was built with the current EMBED_MODEL (a model change invalidates all)."""
    if not INDEX_PATH.exists():
        return {}
    cache = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    return cache.get("docs", {}) if cache.get("model") == EMBED_MODEL else {}


def hydrate(docs: list[Doc], cached: dict) -> list[Doc]:
    """Attach cached vectors/graph to docs whose content hash still matches; return
    the docs that MISSED the cache (new or changed) for the caller to embed."""
    misses = []
    for d in docs:
        prev = cached.get(d.path)
        if prev and prev.get("hash") == d.content_hash and prev.get("vector"):
            d.vector = prev["vector"]
            d.entities = prev.get("entities", [])
            d.relations = prev.get("relations", [])
        else:
            misses.append(d)
    return misses
