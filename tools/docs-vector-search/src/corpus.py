"""Corpus loading + chunking: split docs into heading-aware, token-windowed
passages so retrieval and reranking work on granular spans."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import frontmatter
from markdown_it import MarkdownIt

from .config import CHUNK_OVERLAP, CHUNK_TOKENS, CORPUS_DIR

_MARKDOWN = MarkdownIt().enable("table")
_BLOCK_TYPES = frozenset(
    {
        "paragraph_open",
        "blockquote_open",
        "bullet_list_open",
        "ordered_list_open",
        "table_open",
        "fence",
        "code_block",
        "html_block",
        "hr",
    }
)
_ATOMIC_BLOCK_TYPES = frozenset(_BLOCK_TYPES - {"paragraph_open"})


@dataclass
class Chunk:
    id: str  # "<repo-relative path>#<ordinal>"
    path: str
    title: str
    heading: str
    ordinal: int
    text: str
    content_hash: str


@dataclass(frozen=True)
class _MarkdownBlock:
    text: str
    heading_path: tuple[str, ...]
    atomic: bool


def _title(post, path: Path) -> str:
    if post.get("title"):
        return str(post["title"])
    tokens = _MARKDOWN.parse(post.content)
    for index, token in enumerate(tokens[:-1]):
        if token.type == "heading_open" and token.level == 0 and token.tag == "h1":
            if tokens[index + 1].type == "inline" and tokens[index + 1].content.strip():
                return tokens[index + 1].content.strip()
    return path.stem


def _windows(text: str, max_tokens: int, overlap: int) -> list[str]:
    """Greedy word-window split by approximate tokens (~4 chars/token) with overlap."""
    words = text.split()
    if not words:
        return []
    max_chars = max(1, max_tokens * 4)
    ov_chars = max(0, min(overlap * 4, max_chars - 1))
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


def _parse_blocks(content: str) -> list[_MarkdownBlock]:
    lines = content.splitlines(keepends=True)
    tokens = _MARKDOWN.parse(content)
    heading_path: list[str] = []
    blocks: list[_MarkdownBlock] = []

    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.level == 0:
            level = int(token.tag[1:])
            heading = ""
            if index + 1 < len(tokens) and tokens[index + 1].type == "inline":
                heading = tokens[index + 1].content.strip()
            if heading:
                del heading_path[level - 1 :]
                heading_path.append(heading)
            continue

        if token.level != 0 or token.type not in _BLOCK_TYPES or not token.map:
            continue
        start, end = token.map
        text = "".join(lines[start:end]).strip()
        if text:
            blocks.append(
                _MarkdownBlock(
                    text=text,
                    heading_path=tuple(heading_path),
                    atomic=token.type in _ATOMIC_BLOCK_TYPES,
                )
            )
    return blocks


def _overlap_tail(blocks: list[_MarkdownBlock], max_chars: int) -> list[_MarkdownBlock]:
    if max_chars <= 0:
        return []
    tail: list[_MarkdownBlock] = []
    used = 0
    for block in reversed(blocks):
        extra = len(block.text) + (2 if tail else 0)
        if tail and used + extra > max_chars:
            break
        tail.insert(0, block)
        used += extra
    return tail


def _heading_label(title: str, heading_path: tuple[str, ...]) -> str:
    if heading_path and heading_path[0] == title:
        heading_path = heading_path[1:]
    return " > ".join(heading_path) or title


def _pack_blocks(
    blocks: list[_MarkdownBlock],
    label: str,
    max_tokens: int,
    overlap: int,
) -> list[str]:
    max_chars = max(1, max_tokens * 4)
    overlap_chars = max(0, overlap * 4)
    prepared: list[_MarkdownBlock] = []
    for block in blocks:
        if block.atomic or len(block.text) <= max_chars:
            prepared.append(block)
            continue
        prepared.extend(
            _MarkdownBlock(piece, block.heading_path, atomic=False)
            for piece in _windows(block.text, max_tokens, overlap)
        )

    chunks: list[str] = []
    current: list[_MarkdownBlock] = []
    current_len = 0
    for block in prepared:
        block_len = len(block.text)
        if current and current_len + 2 + block_len > max_chars:
            body = "\n\n".join(b.text for b in current)
            chunks.append(f"{label}\n\n{body}")
            current = _overlap_tail(current, overlap_chars)
            current_len = sum(len(item.text) for item in current) + max(0, len(current) - 1) * 2
            if current and current_len + 2 + block_len > max_chars:
                current = []
                current_len = 0
        current.append(block)
        current_len += block_len + (2 if len(current) > 1 else 0)

    if current:
        body = "\n\n".join(b.text for b in current)
        chunks.append(f"{label}\n\n{body}")
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def chunk_doc(path: Path) -> list[Chunk]:
    post = frontmatter.loads(path.read_text(encoding="utf-8"))
    # Anchor the id/path to the corpus dir (stable across repo vs container, where the
    # corpus is mounted at /app/corpus) — REPO_ROOT isn't an ancestor of CORPUS_DIR there.
    rel = path.relative_to(CORPUS_DIR).as_posix()
    title = _title(post, path)

    blocks = _parse_blocks(post.content)
    chunks: list[Chunk] = []
    ordinal = 0
    section_blocks: list[_MarkdownBlock] = []
    section_path: tuple[str, ...] | None = None

    def flush_section() -> None:
        nonlocal ordinal, section_blocks
        if not section_blocks:
            return
        heading = _heading_label(title, section_path or ())
        for piece in _pack_blocks(section_blocks, heading, CHUNK_TOKENS, CHUNK_OVERLAP):
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
        section_blocks = []

    for block in blocks:
        if section_path is None:
            section_path = block.heading_path
        elif block.heading_path != section_path:
            flush_section()
            section_path = block.heading_path
        section_blocks.append(block)
    flush_section()
    return chunks


def load_chunks() -> list[Chunk]:
    """Load and chunk only regular files whose extension is exactly `.md`."""
    chunks = []
    # Scan all files explicitly so non-Markdown sidecars, uppercase extensions, and
    # directories named like Markdown files never enter the indexing pipeline.
    markdown_files = sorted(
        path
        for path in CORPUS_DIR.rglob("*")
        if path.is_file() and path.suffix == ".md"
    )
    for path in markdown_files:
        chunks.extend(chunk_doc(path))
    return chunks
