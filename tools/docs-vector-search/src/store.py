"""Vector store — pgvector on the VNGCloud vDB (PostgreSQL 15).

`connect()` gives a ready connection (extension + vector adapter registered);
`init_schema()` creates the table + HNSW index (enrich only). Search uses cosine
distance (`<=>`).
"""
from __future__ import annotations

from .config import EMBED_DIM, PG_SCHEMA, RETRIEVE_TOP_N, pg_dsn


def connect():
    import psycopg
    from pgvector.psycopg import register_vector

    conn = psycopg.connect(pg_dsn(), autocommit=True)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")  # idempotent; needed before register
    register_vector(conn)
    return conn


def init_schema(conn) -> None:
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {PG_SCHEMA}")
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {PG_SCHEMA}.doc_chunks (
            id           text PRIMARY KEY,
            path         text NOT NULL,
            title        text,
            heading      text,
            ordinal      int,
            content_hash text,
            text         text NOT NULL,
            embedding    vector({EMBED_DIM}) NOT NULL
        )"""
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS doc_chunks_embed_idx "
        f"ON {PG_SCHEMA}.doc_chunks USING hnsw (embedding vector_cosine_ops)"
    )


def existing_hashes(conn) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT id, content_hash FROM {PG_SCHEMA}.doc_chunks")
        return dict(cur.fetchall())


def upsert(conn, rows: list[tuple]) -> None:
    """rows: (id, path, title, heading, ordinal, content_hash, text, embedding)."""
    from pgvector import Vector

    # pgvector registers dumpers for Vector/ndarray only — a bare list would be
    # sent as float8[] and rejected by the vector(384) column. Wrap the embedding.
    rows = [(*r[:7], Vector(r[7])) for r in rows]
    with conn.cursor() as cur:
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


def prune(conn, keep_ids: list[str]) -> int:
    """Delete chunks whose id is no longer present in the corpus.

    Refuses to run on an empty keep-set: `WHERE NOT (id = ANY('{}'))` matches every row,
    so an empty/mis-mounted corpus would silently wipe the whole index. Callers must guard
    the empty case explicitly (see enrich.build); this is the last-line safety net.
    """
    if not keep_ids:
        return 0
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {PG_SCHEMA}.doc_chunks WHERE NOT (id = ANY(%s))", (keep_ids,))
        return cur.rowcount


def count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {PG_SCHEMA}.doc_chunks")
        return cur.fetchone()[0]


def search(conn, qvec: list[float], n: int = RETRIEVE_TOP_N) -> list[dict]:
    from pgvector import Vector

    qv = Vector(qvec)  # same reason as upsert: the <=> operand must dump as a vector, not a list
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT id, path, title, heading, text, 1 - (embedding <=> %s) AS score
                FROM {PG_SCHEMA}.doc_chunks
                ORDER BY embedding <=> %s
                LIMIT %s""",
            (qv, qv, n),
        )
        cols = ("id", "path", "title", "heading", "text", "score")
        return [dict(zip(cols, r)) for r in cur.fetchall()]
