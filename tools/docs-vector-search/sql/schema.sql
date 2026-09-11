-- pgvector schema for the Document Vector Search agent, on the vDB (PostgreSQL 15).
-- Apply once per environment (UAT vDB, PROD vDB), e.g. via deployments/postgres/run-sql.sh.
-- enrich (src/store.py) also creates these idempotently; this file is for ops / review.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS rag;

CREATE TABLE IF NOT EXISTS rag.doc_chunks (
    id           text PRIMARY KEY,     -- "<repo-relative path>#<ordinal>"
    path         text NOT NULL,
    title        text,
    heading      text,
    ordinal      int,
    content_hash text,
    text         text NOT NULL,
    embedding    vector(384) NOT NULL   -- keep in sync with the selected provider's embedding dimensions
);

CREATE INDEX IF NOT EXISTS doc_chunks_embed_idx
    ON rag.doc_chunks USING hnsw (embedding vector_cosine_ops);
