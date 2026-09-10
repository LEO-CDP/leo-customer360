# docs-vector-search

Local-model Graph-RAG over the LEO Customer 360 docs corpus. Semantic search + a
grounded question-answering agent, **fully local** (no hosted LLM), backed by
**pgvector** on the VNGCloud **vDB**.

Design + rationale: [`deployments/docs/local-rag-implementation-plan.md`](../../deployments/docs/local-rag-implementation-plan.md).

## Pipeline

```
docs/ *.md
  │  chunk (heading-aware + token window)
  │  embed passages — e5-small (fastembed/ONNX)
  ▼  upsert → pgvector on the vDB
enrich.py

question → embed "query:" → pgvector top-20 → rerank (bge, top-5) → generate (Qwen2.5-0.5B) → answer + sources
  agent.py / server.py
```

Local model seams (`src/providers.py`), all lazy-loaded once:
- **embed** — `intfloat/multilingual-e5-small` via fastembed (VN+EN; `query:`/`passage:` prefixes)
- **rerank** — `BAAI/bge-reranker-base` via fastembed `TextCrossEncoder`
- **generate** — `Qwen2.5-0.5B-Instruct` Q4 via `llama-cpp-python`

Vector store (`src/store.py`) — `rag.doc_chunks` table with a `vector(384)` column + HNSW cosine index.

## Prerequisites

- PostgreSQL 15 (the vDB) with the **`vector` extension** available (`enrich` runs `CREATE EXTENSION IF NOT EXISTS vector`).
- Python 3.12; a C toolchain for `llama-cpp-python` (the Dockerfile installs it).
- Model weights downloaded into `./models/` (the Qwen GGUF; fastembed auto-downloads its ONNX models on first use into `./models/fastembed`).

## Quick start

```bash
cd tools/docs-vector-search
python -m venv .venv && . .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env                 # set PG_* (the vDB) and model paths

# Fetch the generator once (example):
#   huggingface-cli download Qwen/Qwen2.5-0.5B-Instruct-GGUF \
#     Qwen2.5-0.5B-Instruct-Q4_K_M.gguf --local-dir ./models

python -m src.enrich                 # chunk → embed → upsert into pgvector
python -m src.agent "How does identity resolution merge two profiles?"
uvicorn src.server:app --port 8000

curl -s localhost:8000/health
curl -s localhost:8000/search -H 'content-type: application/json' -d '{"query":"tenant isolation"}'
curl -s localhost:8000/ask    -H 'content-type: application/json' -d '{"question":"What is CIR?"}'
```

## Commands

| Command | Does |
|---------|------|
| `python -m src.enrich` | chunk + embed changed chunks → upsert into pgvector; prune removed chunks (idempotent) |
| `python -m src.enrich --dry-run` | report what would change, write nothing |
| `python -m src.agent "…"` | one-shot question; or no args for a REPL |
| `uvicorn src.server:app` | serve `/ask`, `/search`, `/health` |

## Endpoints

| Endpoint | Body | Returns |
|----------|------|---------|
| `POST /ask` | `{question, top_n?, top_k?}` | grounded answer + cited sources |
| `POST /search` | `{query, top_n?}` | reranked chunks (no generation) |
| `GET /health` | — | chunk count, models |

## Notes

- **Idempotent enrich:** a content hash per chunk skips unchanged chunks. Changing `EMBED_MODEL` (or its dim) means you must set `EMBED_DIM` to match and recreate `rag.doc_chunks` (the `vector(N)` column is fixed-width).
- **e5 prefixes:** `embed()` prepends `query:` / `passage:`. If a future fastembed version adds e5 prefixes itself, drop them here to avoid double-prefixing.
- **RAM:** on a 1 vCPU / 2 GB box the vectors live in the vDB (off-box); e5 + reranker + Qwen ≈ 1.4 GB resident — tight, may need swap. Drop the reranker (`DOCS_RERANK_ENABLED=false`) first if memory-constrained.
- **Deploy (UAT/PROD vServer):** [`deployments/server/deploy-docs-search.sh`](../../deployments/server/deploy-docs-search.sh) — pulls the CI-built GHCR image onto the dedicated `docs` box, runs `enrich`, serves. Wired into CD as the `docs-search` step; the box is defined in [`deployments/server/overlays`](../../deployments/server/overlays).
- **Local dev (Docker):** [`docker-compose.yml`](docker-compose.yml) here — `docker compose run --rm docs-vector-search python -m src.enrich`, then `docker compose up`.
