"""Document chunk repository — pgvector and PostgreSQL full-text search.

`connect()` gives a ready connection (extension + vector adapter registered);
`DocumentChunkRepository` owns persistence and candidate generation. Vector search
uses cosine distance (`<=>`); keyword search uses a multilingual-safe `simple`
`tsvector` index. The two ranked lists are merged with reciprocal-rank fusion.
"""
from __future__ import annotations

import psycopg
from pgvector.psycopg import register_vector

from .retrieval import ReciprocalRankFusion
from .config import (
    EMBED_DIM,
    HYBRID_SEARCH_ENABLED,
    KEYWORD_SEARCH_TOP_N,
    PG_SCHEMA,
    RETRIEVE_TOP_N,
    RRF_RANK_CONSTANT,
    pg_dsn,
)


def connect():
    conn = psycopg.connect(pg_dsn(), autocommit=True)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")  # idempotent; needed before register
    register_vector(conn)
    return conn


class DocumentChunkRepository:
    """Persistence and candidate-generation boundary for document chunks."""

    def __init__(self, connection):
        self.connection = connection
        self._fusion = ReciprocalRankFusion(RRF_RANK_CONSTANT)

    def init_schema(self) -> None:
        self.connection.execute(f"CREATE SCHEMA IF NOT EXISTS {PG_SCHEMA}")
        self.connection.execute(
            f"""CREATE TABLE IF NOT EXISTS {PG_SCHEMA}.doc_chunks (
                id           text PRIMARY KEY,
                path         text NOT NULL,
                title        text,
                heading      text,
                ordinal      int,
                content_hash text,
                text         text NOT NULL,
                embedding    vector({EMBED_DIM}) NOT NULL,
                search_vector tsvector GENERATED ALWAYS AS (
                    setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
                    setweight(to_tsvector('simple', coalesce(heading, '')), 'B') ||
                    setweight(to_tsvector('simple', coalesce(text, '')), 'C')
                ) STORED
            )"""
        )
        # Migrate indexes created before hybrid retrieval without requiring a manual
        # destructive rebuild. The generated column also backfills existing rows.
        self.connection.execute(
            f"""ALTER TABLE {PG_SCHEMA}.doc_chunks
                ADD COLUMN IF NOT EXISTS search_vector tsvector GENERATED ALWAYS AS (
                    setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
                    setweight(to_tsvector('simple', coalesce(heading, '')), 'B') ||
                    setweight(to_tsvector('simple', coalesce(text, '')), 'C')
                ) STORED"""
        )
        self.connection.execute(
            f"CREATE INDEX IF NOT EXISTS doc_chunks_embed_idx "
            f"ON {PG_SCHEMA}.doc_chunks USING hnsw (embedding vector_cosine_ops)"
        )
        self.connection.execute(
            f"CREATE INDEX IF NOT EXISTS doc_chunks_search_idx "
            f"ON {PG_SCHEMA}.doc_chunks USING gin (search_vector)"
        )

    def existing_hashes(self) -> dict[str, str]:
        with self.connection.cursor() as cur:
            cur.execute(f"SELECT id, content_hash FROM {PG_SCHEMA}.doc_chunks")
            return dict(cur.fetchall())

    def upsert(self, rows: list[tuple]) -> None:
        """rows: (id, path, title, heading, ordinal, content_hash, text, embedding)."""
        from pgvector import Vector

        # pgvector registers dumpers for Vector/ndarray only — a bare list would be
        # sent as float8[] and rejected by the vector column. Wrap the embedding.
        rows = [(*row[:7], Vector(row[7])) for row in rows]
        with self.connection.cursor() as cur:
            cur.executemany(
                f"""INSERT INTO {PG_SCHEMA}.doc_chunks
                      (id, path, title, heading, ordinal, content_hash, text, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                      path=EXCLUDED.path, title=EXCLUDED.title, heading=EXCLUDED.heading,
                      ordinal=EXCLUDED.ordinal, content_hash=EXCLUDED.content_hash,
                      text=EXCLUDED.text, embedding=EXCLUDED.embedding""",
                rows,
            )

    def prune(self, keep_ids: list[str]) -> int:
        """Delete chunks absent from the current corpus; never prune an empty set."""
        if not keep_ids:
            return 0
        with self.connection.cursor() as cur:
            cur.execute(
                f"DELETE FROM {PG_SCHEMA}.doc_chunks WHERE NOT (id = ANY(%s))",
                (keep_ids,),
            )
            return cur.rowcount

    def count(self) -> int:
        with self.connection.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {PG_SCHEMA}.doc_chunks")
            return cur.fetchone()[0]

    def vector_search(self, qvec: list[float], limit: int = RETRIEVE_TOP_N) -> list[dict]:
        from pgvector import Vector

        qv = Vector(qvec)
        with self.connection.cursor() as cur:
            cur.execute(
                f"""SELECT id, path, title, heading, text,
                           1 - (embedding <=> %s) AS score
                    FROM {PG_SCHEMA}.doc_chunks
                    ORDER BY embedding <=> %s
                    LIMIT %s""",
                (qv, qv, limit),
            )
            return self._rows(cur.fetchall(), ("id", "path", "title", "heading", "text", "score"))

    def keyword_search(self, query: str, limit: int = RETRIEVE_TOP_N) -> list[dict]:
        if not query.strip():
            return []
        with self.connection.cursor() as cur:
            cur.execute(
                f"""WITH parsed_query AS (
                           SELECT websearch_to_tsquery('simple', %s) AS tsquery
                       )
                    SELECT d.id, d.path, d.title, d.heading, d.text,
                           ts_rank_cd(d.search_vector, q.tsquery, 32) AS score
                    FROM {PG_SCHEMA}.doc_chunks AS d
                    CROSS JOIN parsed_query AS q
                    WHERE d.search_vector @@ q.tsquery
                    ORDER BY score DESC, d.ordinal ASC
                    LIMIT %s""",
                (query, limit),
            )
            return self._rows(
                cur.fetchall(),
                ("id", "path", "title", "heading", "text", "keyword_score"),
            )

    def hybrid_search(
        self,
        query: str,
        qvec: list[float],
        limit: int = RETRIEVE_TOP_N,
        keyword_limit: int | None = None,
    ) -> list[dict]:
        vector_hits = self.vector_search(qvec, limit)
        keyword_hits = self.keyword_search(query, keyword_limit or KEYWORD_SEARCH_TOP_N)
        return self._fusion.fuse(
            (("vector", vector_hits), ("keyword", keyword_hits)), limit
        )

    def retrieve(
        self,
        query: str,
        qvec: list[float],
        limit: int = RETRIEVE_TOP_N,
        keyword_limit: int | None = None,
    ) -> list[dict]:
        if HYBRID_SEARCH_ENABLED:
            return self.hybrid_search(query, qvec, limit, keyword_limit)
        return self.vector_search(qvec, limit)

    @staticmethod
    def _rows(rows: list[tuple], columns: tuple[str, ...]) -> list[dict]:
        return [dict(zip(columns, row)) for row in rows]


# Compatibility adapters for enrich, server integrations, and external scripts.
def init_schema(conn) -> None:
    DocumentChunkRepository(conn).init_schema()


def existing_hashes(conn) -> dict[str, str]:
    return DocumentChunkRepository(conn).existing_hashes()


def upsert(conn, rows: list[tuple]) -> None:
    DocumentChunkRepository(conn).upsert(rows)


def prune(conn, keep_ids: list[str]) -> int:
    return DocumentChunkRepository(conn).prune(keep_ids)


def count(conn) -> int:
    return DocumentChunkRepository(conn).count()


def search(conn, qvec: list[float], n: int = RETRIEVE_TOP_N) -> list[dict]:
    return DocumentChunkRepository(conn).vector_search(qvec, n)


def hybrid_search(
    conn,
    query: str,
    qvec: list[float],
    n: int = RETRIEVE_TOP_N,
    keyword_n: int | None = None,
) -> list[dict]:
    return DocumentChunkRepository(conn).hybrid_search(query, qvec, n, keyword_n)
