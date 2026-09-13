# docs-vector-search

Provider-agnostic Graph-RAG over the LEO Customer 360 documentation corpus. The service
provides semantic search and grounded question answering backed by **pgvector** on the
VNGCloud **vDB**. OpenAI is the default provider; Gemini and local open-source models such
as Qwen are optional and can be selected independently for embeddings, reranking, and
generation.

Design + rationale: [`deployments/docs/local-rag-implementation-plan.md`](../../deployments/docs/local-rag-implementation-plan.md).

## Pipeline

```
docs/ *.md
  │  chunk (heading-aware + token window)
  │  embed passages — OpenAI / Gemini / fastembed
  ▼  upsert → pgvector on the vDB
enrich.py

question → embed ─┬→ pgvector candidates ─┐
                  └→ PostgreSQL FTS ──────┴→ RRF fusion → rerank → grounded generation
  agent.py / server.py
```

`src.corpus` scans `CORPUS_DIR` recursively and accepts only regular files with the exact
lowercase `.md` extension. It parses those files into structural blocks, preserves frontmatter
titles and full heading paths, and splits each section into overlapping windows without breaking tables,
lists, or fenced code blocks. `src.enrich` hashes each chunk,
embeds only changed chunks, upserts them into `rag.doc_chunks`, and prunes chunks removed
from the corpus. Retrieval combines semantic vector candidates with PostgreSQL full-text
keyword candidates using reciprocal-rank fusion (RRF), then optionally reranks the merged
pool. The FTS index uses the `simple` configuration so English and Vietnamese terms,
including diacritics and product names, are indexed without English stop-word or stemming
assumptions. `/search` returns ranked chunks without generation, while `/ask` sends only the
selected context to the answer model and returns the exact context and sources used.

Provider seams (`src/providers.py`), with local models loaded lazily:
- **embed** — OpenAI `text-embedding-3-small` by default; Gemini `gemini-embedding-001` or fastembed locally
- **rerank** — OpenAI `gpt-5-nano` in one batched request by default; local `BAAI/bge-reranker-base` via fastembed is available with `DOCS_RERANK_PROVIDER=local`
- **generate** — OpenAI `gpt-5.6-luna` by default; Gemini `gemini-2.5-flash` or Qwen GGUF locally
- **request limiting** — Redis-backed atomic IP and browser sliding-window buckets shared across workers; local Docker runs a dedicated no-auth Redis service (`docs-rate-limit-redis`) for `/ask` and `/search`

Document repository (`src/store.py`) — `rag.doc_chunks` has a provider-selected `vector(384)`
column with an HNSW cosine index and a generated `tsvector` column with a GIN index. The
default hosted OpenAI and Gemini configurations both request 384 dimensions; local embeddings
must also produce 384 dimensions.

## Prerequisites

- PostgreSQL 15 (the vDB) with the **`vector` extension** available (`enrich` runs `CREATE EXTENSION IF NOT EXISTS vector`).
- Python 3.12; a C/C++ toolchain is needed only when installing `llama-cpp-python` for local Qwen generation.
- The default Docker/CI image is the `hosted` target and does not include local ML packages or model weights.
  Set `DOCS_IMAGE_TARGET=local` when using a local embedding, reranker, or Qwen provider.
- Redis is required when `ASK_RATE_MAX > 0` (the default). If Redis is unavailable, `/ask` and `/search`
  return `503` instead of bypassing the limiter.
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
# Local providers only: pip install -r requirements-local.txt
cp .env.example .env                 # set PG_* (the vDB) and the selected provider key
# Default configuration: set DOCS_OPENAI_API_KEY in .env.

# Fetch the optional local generator once (only when DOCS_LLM_PROVIDER=local):
#   huggingface-cli download Qwen/Qwen2.5-0.5B-Instruct-GGUF \
#     Qwen2.5-0.5B-Instruct-Q4_K_M.gguf --local-dir ./models

python -m src.enrich                 # scan .md → chunk → embed → upsert into pgvector + FTS
python -m src.enrich --dry-run       # report changes without writing the index
python -m src.agent "How does identity resolution merge two profiles?"
uvicorn src.server:app --port 8001

curl -s localhost:8001/health
curl -s localhost:8001/search -H 'content-type: application/json' -d '{"query":"tenant isolation"}'
curl -s localhost:8001/ask    -H 'content-type: application/json' -d '{"question":"What is CIR?"}'
# INTERNAL_API_SECRET must be configured for these administrative calls.
curl -s -X POST localhost:8001/reindex -H "X-Internal-Auth: $INTERNAL_API_SECRET"
# Use the returned job id to poll until status is completed or failed.
curl -s localhost:8001/reindex/<job_id> -H "X-Internal-Auth: $INTERNAL_API_SECRET"
```

## Commands

| Command | Does |
|---------|------|
| `python -m src.enrich` | scan only `.md` files, chunk + embed changed chunks → upsert into pgvector/FTS; prune removed chunks (idempotent) |
| `python -m src.enrich --dry-run` | report what would change, write nothing |
| `python -m src.agent "…"` | one-shot question; or no args for a REPL |
| `uvicorn src.server:app --host 0.0.0.0 --port 8001` | serve `/ask`, `/search`, `/health` |
| `bash run_unit_tests.sh` | byte-compile every module without importing heavy local-provider dependencies |

## Endpoints

| Endpoint | Body | Returns |
|----------|------|---------|
| `POST /ask` | `{question, top_n?, top_k?}` | grounded answer + cited sources |
| `POST /search` | `{query, top_n?}` | reranked chunks (no generation) |
| `POST /reindex` | no body; `X-Internal-Auth` required | `202` with an asynchronous job record |
| `GET /reindex/{job_id}` | `X-Internal-Auth` required | job status, counts, and error/result |
| `GET /health` | — | loaded chunk count and active provider/model configuration |

`question` and `query` are limited to 2,000 characters by default. `top_n` is capped at
50 and `top_k` at 20; the defaults are `top_n = 40`, `top_k = 8`, with 5 chunks sent to the generator. Both `/ask` and `/search`
are protected by the Redis limiter unless a valid `X-Internal-Auth` secret is configured
for a trusted internal caller. CORS uses an exact origin allow-list and does not enable
credentials. The API does not expose an unauthenticated wildcard CORS policy.

`POST /reindex` scans the server's configured `CORPUS_DIR`, indexes only lowercase `.md`
files, and runs asynchronously so it does not block the request worker. A second request while
one job is queued or running returns `409` with the active `job_id`. Reindex is restricted to
the configured `INTERNAL_API_SECRET`; the secret should be supplied only by a trusted backend
client, never by browser JavaScript. Poll `GET /reindex/{job_id}` for `completed` or `failed`
and inspect the returned counts.

## Notes

- **Idempotent enrich:** a content hash per chunk skips unchanged chunks. A missing or empty corpus is rejected and cannot prune the existing index. Changing embedding provider/model/dimension requires a matching vector dimension and a full rebuild of `rag.doc_chunks`; vectors from different embedding models are not interchangeable, even at the same dimension.
- **Hybrid retrieval:** `HYBRID_SEARCH_ENABLED=true` combines vector candidates with up to `KEYWORD_SEARCH_TOP_N` exact-term candidates. `RRF_RANK_CONSTANT` controls how quickly rank influence decays. Run `python -m src.enrich` once after deploying this version so existing rows receive the generated FTS values and GIN index.
- **Independent providers:** `DOCS_EMBEDDING_PROVIDER` and `DOCS_LLM_PROVIDER` each accept `openai`, `gemini`, or `local`; reranking accepts `openai` or `local`. Provider names also accept `google`, `google_gemini`, and `google-gemini` as aliases for Gemini.
- **Reranking:** OpenAI reranking makes one batched request for the complete candidate pool. Its response must contain exactly one finite score from 0 to 100 per candidate, in original order, and the request has an 8-second default timeout. If it fails, `DOCS_RERANK_OPENAI_FALLBACK=vector` preserves pgvector order without local BGE; set it to `local` when quality is preferred over latency. Local BGE is slower and is loaded only when selected.
- **Grounding and prompt safety:** the answer agent fences retrieved documents and the user question as untrusted data, strips fence tokens, and instructs the generator to answer only from retrieved context. When the context is insufficient, it returns the documented exact "I don't know" response rather than using outside knowledge.
- **Generation reliability:** hosted OpenAI/Gemini models use `DOCS_HOSTED_LLM_MAX_OUTPUT_TOKENS=1024` by default so reasoning cannot consume the entire visible-answer budget. Local Qwen uses `DOCS_LOCAL_LLM_MAX_OUTPUT_TOKENS=512` with `LOCAL_LLM_CONTEXT_TOKENS=2048` so prompt space remains available. If OpenAI still returns no visible text, the service retries once with at least 2,048 tokens and then raises an explicit diagnostic error instead of returning HTTP 200 with an empty `answer`. The prompt requires English/Vietnamese language matching, concise grounded output, source-title citations, and a non-empty response.
- **Observability:** the runtime Docker image includes OpenTelemetry auto-instrumentation for FastAPI and psycopg. Deployment emits OTLP settings for request tracing; tracing can be disabled with the deployment's `OTEL_ENABLED` configuration.
- **e5 prefixes:** when a local e5 model is selected, `embed()` prepends `query:` / `passage:`. If a future fastembed version adds e5 prefixes itself, drop them here to avoid double-prefixing.
- **RAM:** on a 1 vCPU / 2 GB box the vectors live in the vDB (off-box); local fastembed + reranker + Qwen can reach ≈ 1.4 GB resident — tight, may need swap. Hosted OpenAI/Gemini generation avoids the Qwen footprint. Drop the reranker (`DOCS_RERANK_ENABLED=false`) first if memory-constrained.
- **Deploy (UAT/PROD vServer):** [`deployments/server/deploy-docs-search.sh`](../../deployments/server/deploy-docs-search.sh) — resolves the dedicated `docs` VM, pulls the CI-built GHCR `hosted` image by default, ships the `docs/` corpus, starts a dedicated no-auth local Redis container (`customer360-docs-rate-limit-redis`), runs `enrich` on the VM, and starts the server. The script can build locally with `BUILD_LOCAL=1`; local providers require `DOCS_IMAGE_TARGET=local`. It is wired into CD as the `docs-search` step, and the VM is defined in [`deployments/server/overlays`](../../deployments/server/overlays).
- **Local dev (Docker):** [`docker-compose.yml`](docker-compose.yml) here — includes a dedicated no-auth Redis service (`docs-rate-limit-redis`) on the same Docker network, so rate limiting works without shared stack credentials.
- **Docker image targets:** `docker build --target hosted .` builds the small production image.
  Set `DOCS_IMAGE_TARGET=local` before `docker compose --profile cpu build` or
  `docker compose --profile gpu build` to include fastembed, `llama-cpp-python`, and
  pre-baked embedding/reranking weights. Compose serves only; build or refresh the index first:
  `docker compose --profile cpu run --rm docs-vector-search python -m src.enrich`.
- **Deployment order:** index before serving. The image's default command serves the API;
  deployment runs `python -m src.enrich` as a separate, temporary container so indexing does
  not compete with the long-lived server for memory.
