# docs-vector-search

Provider-agnostic Graph-RAG over the LEO Customer 360 docs corpus. Semantic search + a
grounded question-answering agent backed by **pgvector** on the VNGCloud **vDB**.
OpenAI is the default; Gemini and local open-source models such as Qwen are optional.

Design + rationale: [`deployments/docs/local-rag-implementation-plan.md`](../../deployments/docs/local-rag-implementation-plan.md).

## Pipeline

```
docs/ *.md
  │  chunk (heading-aware + token window)
  │  embed passages — OpenAI / Gemini / fastembed
  ▼  upsert → pgvector on the vDB
enrich.py

question → embed → pgvector top-N → rerank (bge, top-K) → generate (OpenAI / Gemini / Qwen) → answer + sources
  agent.py / server.py
```

Provider seams (`src/providers.py`), all loaded lazily:
- **embed** — OpenAI `text-embedding-3-small` by default; Gemini `gemini-embedding-001` or fastembed locally
- **rerank** — OpenAI `gpt-4o-mini` in one batched request by default; local `BAAI/bge-reranker-base` via fastembed is available with `DOCS_RERANK_PROVIDER=local`
- **generate** — OpenAI `gpt-5.6-luna` by default; Gemini `gemini-2.5-flash` or Qwen GGUF locally
- **request limiting** — Redis-backed atomic IP and browser buckets shared across workers; local Docker runs a dedicated no-auth Redis service (`docs-rate-limit-redis`) for `/ask` and `/search`

Vector store (`src/store.py`) — `rag.doc_chunks` table with a `vector(384)` column + HNSW cosine index.

## Prerequisites

- PostgreSQL 15 (the vDB) with the **`vector` extension** available (`enrich` runs `CREATE EXTENSION IF NOT EXISTS vector`).
- Python 3.12; a C toolchain for `llama-cpp-python` only when using local Qwen generation.
- **Default run configuration:** `DOCS_EMBEDDING_PROVIDER=openai` and `DOCS_LLM_PROVIDER=openai`.
  Set `DOCS_OPENAI_API_KEY` in `.env` before running `enrich`, `/search`, or `/ask`.
- Gemini mode requires `DOCS_GEMINI_API_KEY` when selected.
- Local mode downloads fastembed weights into `./models/fastembed`; local Qwen generation additionally needs its GGUF in `./models/`.

Provider configuration is explicit: use `DOCS_OPENAI_EMBEDDING_MODEL` and
`DOCS_OPENAI_LLM_MODEL` plus `DOCS_OPENAI_RERANK_MODEL` for OpenAI, `DOCS_GEMINI_EMBEDDING_MODEL` and
`DOCS_GEMINI_LLM_MODEL` for Gemini, and `DOCS_LOCAL_EMBEDDING_MODEL` plus
`DOCS_LOCAL_LLM_MODEL_PATH` for local models. The active provider is selected by
`DOCS_EMBEDDING_PROVIDER`, `DOCS_RERANK_PROVIDER`, and `DOCS_LLM_PROVIDER`.

## Quick start

```bash
cd tools/docs-vector-search
python -m venv .venv && . .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env                 # set PG_* (the vDB) and the selected provider key
# Default configuration: set DOCS_OPENAI_API_KEY in .env.

# Fetch the optional local generator once (only when DOCS_LLM_PROVIDER=local):
#   huggingface-cli download Qwen/Qwen2.5-0.5B-Instruct-GGUF \
#     Qwen2.5-0.5B-Instruct-Q4_K_M.gguf --local-dir ./models

python -m src.enrich                 # chunk → embed → upsert into pgvector
python -m src.agent "How does identity resolution merge two profiles?"
uvicorn src.server:app --port 8001

curl -s localhost:8001/health
curl -s localhost:8001/search -H 'content-type: application/json' -d '{"query":"tenant isolation"}'
curl -s localhost:8001/ask    -H 'content-type: application/json' -d '{"question":"What is CIR?"}'
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

- **Idempotent enrich:** a content hash per chunk skips unchanged chunks. Changing embedding provider/model/dim means you must set the matching dimensions and recreate or fully rebuild `rag.doc_chunks` (vectors from different embedding models are not interchangeable, even at the same dimension).
- **Independent providers:** `DOCS_EMBEDDING_PROVIDER` and `DOCS_LLM_PROVIDER` each accept `openai`, `gemini`, or `local`; reranking accepts `openai` or `local`. OpenAI reranking makes one request for the full candidate pool, avoiding the 54-second local BGE CPU path. If that request fails, `DOCS_RERANK_OPENAI_FALLBACK=vector` keeps the pgvector order without invoking local BGE; set it to `local` when quality is preferred over latency.
- **OpenAI rerank safety:** candidate text is sent to the configured OpenAI-compatible endpoint as untrusted data and the response must contain exactly one finite score from 0 to 100 per candidate. The hosted call has its own short timeout (`DOCS_OPENAI_RERANK_TIMEOUT_SECONDS`, default 8 seconds), and the service never warms it at startup.
- **e5 prefixes:** when a local e5 model is selected, `embed()` prepends `query:` / `passage:`. If a future fastembed version adds e5 prefixes itself, drop them here to avoid double-prefixing.
- **RAM:** on a 1 vCPU / 2 GB box the vectors live in the vDB (off-box); local fastembed + reranker + Qwen can reach ≈ 1.4 GB resident — tight, may need swap. Hosted OpenAI/Gemini generation avoids the Qwen footprint. Drop the reranker (`DOCS_RERANK_ENABLED=false`) first if memory-constrained.
- **Deploy (UAT/PROD vServer):** [`deployments/server/deploy-docs-search.sh`](../../deployments/server/deploy-docs-search.sh) — pulls the CI-built GHCR image onto the dedicated `docs` box, starts a dedicated no-auth local Redis container for rate limiting (`customer360-docs-rate-limit-redis`), runs `enrich`, serves. Wired into CD as the `docs-search` step; the box is defined in [`deployments/server/overlays`](../../deployments/server/overlays).
- **Local dev (Docker):** [`docker-compose.yml`](docker-compose.yml) here — includes a dedicated no-auth Redis service (`docs-rate-limit-redis`) on the same Docker network, so rate limiting works without shared stack credentials.
