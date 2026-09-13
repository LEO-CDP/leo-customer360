from __future__ import annotations

from src import corpus


def test_chunk_doc_preserves_markdown_blocks_and_heading_context(tmp_path, monkeypatch):
    path = tmp_path / "guide.md"
    path.write_text(
        """---
title: Retrieval Guide
---

# Retrieval Guide

## Setup

Use the service for documentation search.

| Setting | Value |
| --- | --- |
| overlap | 50 |
| size | 400 |

```python
def retrieve(query):
    return search(query)
```

### Details

Nested heading context should be retained for this paragraph.
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(corpus, "CORPUS_DIR", tmp_path)
    monkeypatch.setattr(corpus, "CHUNK_TOKENS", 8)
    monkeypatch.setattr(corpus, "CHUNK_OVERLAP", 2)

    chunks = corpus.chunk_doc(path)

    table = "| Setting | Value |\n| --- | --- |\n| overlap | 50 |\n| size | 400 |"
    code = "```python\ndef retrieve(query):\n    return search(query)\n```"
    table_chunks = [chunk for chunk in chunks if table in chunk.text]
    code_chunks = [chunk for chunk in chunks if code in chunk.text]
    nested = [chunk for chunk in chunks if chunk.heading == "Setup > Details"]

    assert len(table_chunks) == 1
    assert len(code_chunks) == 1
    assert nested
    assert all(chunk.title == "Retrieval Guide" for chunk in chunks)
    assert all(chunk.text.startswith(f"{chunk.heading}\n\n") for chunk in chunks)


def test_load_chunks_indexes_only_lowercase_md_files(tmp_path, monkeypatch):
    (tmp_path / "included.md").write_text("# Included\n\nThis is indexed.", encoding="utf-8")
    (tmp_path / "ignored.MD").write_text("# Ignored\n\nThis is not indexed.", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("# Ignored\n\nThis is not indexed.", encoding="utf-8")
    (tmp_path / "directory.md").mkdir()

    monkeypatch.setattr(corpus, "CORPUS_DIR", tmp_path)

    chunks = corpus.load_chunks()

    assert chunks
    assert {chunk.path for chunk in chunks} == {"included.md"}