"""Document vector-search agent — local-model Graph-RAG over the docs corpus,
backed by pgvector on the VNGCloud vDB. Chunk → embed (e5) → store (pgvector) →
retrieve → rerank (bge) → generate (Qwen)."""
