"""Corpus loading + chunking: split docs into heading-aware, token-windowed
passages so retrieval and reranking work on granular spans."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import frontmatter

from .config import CHUNK_OVERLAP, CHUNK_TOKENS, CORPUS_DIR, REPO_ROOT

H1_RE = re.compile(r"^#\s+(.+)$", re.M)
HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$")


@dataclass
class Chunk:
    id: str  # "<repo-relative path>#<ordinal>"
    path: str
    title: str
    heading: str
    ordinal: int
    text: str
    content_hash: str


def _title(post, path: Path) -> str:
    if post.get("title"):
        return str(post["title"])
    m = H1_RE.search(post.content)
    return m.group(1).strip() if m else path.stem


def _windows(text: str, max_tokens: int, overlap: int) -> list[str]:
    """Greedy word-window split by approximate tokens (~4 chars/token) with overlap."""
    words = text.split()
    if not words:
        return []
    max_chars, ov_chars = max_tokens * 4, overlap * 4
    out, cur, cur_len = [], [], 0
    for w in words:
        cur.append(w)
        cur_len += len(w) + 1
        if cur_len >= max_chars:
            out.append(" ".join(cur))
            tail, tl = [], 0
            for tw in reversed(cur):  # carry an overlap tail into the next window
                if tl >= ov_chars:
                    break
                tail.insert(0, tw)
                tl += len(tw) + 1
            cur, cur_len = tail, tl
    if cur and (not out or " ".join(cur) != out[-1]):
        out.append(" ".join(cur))
    return out


def chunk_doc(path: Path) -> list[Chunk]:
    post = frontmatter.loads(path.read_text(encoding="utf-8"))
    rel = path.relative_to(REPO_ROOT).as_posix()
    title = _title(post, path)

    # Split the body into (heading, text) sections, then window each section.
    sections: list[tuple[str, list[str]]] = [(title, [])]
    for line in post.content.splitlines():
        m = HEADING_RE.match(line)
        if m:
            sections.append((m.group(1).strip(), []))
        else:
            sections[-1][1].append(line)

    chunks, ordinal = [], 0
    for heading, lines in sections:
        section_text = "\n".join(lines).strip()
        for piece in _windows(section_text, CHUNK_TOKENS, CHUNK_OVERLAP):
            piece = piece.strip()
            if not piece:
                continue
            chunks.append(
                Chunk(
                    id=f"{rel}#{ordinal}",
                    path=rel,
                    title=title,
                    heading=heading,
                    ordinal=ordinal,
                    text=piece,
                    content_hash=hashlib.sha256(piece.encode("utf-8")).hexdigest(),
                )
            )
            ordinal += 1
    return chunks


def load_chunks() -> list[Chunk]:
    chunks = []
    for path in sorted(CORPUS_DIR.rglob("*.md")):
        chunks.extend(chunk_doc(path))
    return chunks
