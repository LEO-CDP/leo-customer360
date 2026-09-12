# docs-vector-search — performance record

Measured performance of the deployed service. Append a dated row to **History** after
each notable run (new corpus size, model change, box resize, RAGAS run).

## Environment (baseline)

| | |
|---|---|
| Box | `c360-api-uat-docs` · `s-general-1x2` (1 vCPU / 2 GB) + 2 GB swap |
| Store | pgvector on the vDB — `rag.doc_chunks`, `vector(384)`, HNSW cosine |
| Corpus | **928 chunks** from `docs/**` (heading-aware + ~400-token windows) |
| Embed | `paraphrase-multilingual-MiniLM-L12-v2` (384-dim, VN+EN), fastembed/ONNX |
| Rerank | `BAAI/bge-reranker-base`, fastembed cross-encoder |
| Generate | `Qwen2.5-0.5B-Instruct` Q4_K_M, llama-cpp-python |
| Local generation tuning | `DOCS_LOCAL_LLM_THREADS=2`, `DOCS_LOCAL_LLM_BATCH_SIZE=512`, memory-mapped GGUF |

## Startup (cold boot)

The server warms embed + rerank in the lifespan; Qwen loads lazily on the first `/ask`.

| Phase | Before model pre-bake | After model pre-bake (baked in image) |
|---|---|---|
| Qwen GGUF fetch (deploy step, curl) | ~6 min first deploy, then host-cached | same (host-cached in `/opt/c360/docs-models`) |
| Reranker download at boot (unauth HF) | **~10 min** (dominant cost) | **0** — baked into the image |
| Embed download at boot | (cached after enrich) | **0** — baked |
| **Time to healthy `:8001`** | **~15 min** (fresh box) | **22 s measured** — cold restart, load from baked cache, no download |

> The reranker download from **unauthenticated** Hugging Face was the killer (~10 min on the
> box). Fix: pre-bake the default embed + rerank models into the image at build time
> (`Dockerfile` → `FASTEMBED_CACHE=/app/model-cache/fastembed`). Alternative: set `HF_TOKEN`.

## Latency (warm)

Measured 2026-09-06 (UAT, first live smoke), `curl` from the box (`localhost:8001`):

| Endpoint | Latency | Notes |
|---|---|---|
| `GET /health` | <100 ms | chunk count + model names |
| `POST /search` | ~3 s | embed query + pgvector top-N + bge rerank (CPU) |
| `POST /ask` (first call) | ~15–42 s | includes lazy Qwen load; grounded answer + 5 sources |
| `POST /ask` (warm) | **~17–42 s** | historical UAT baseline; dominated by Qwen 0.5B generation. Current tuning uses `DOCS_LLM_MAX_OUTPUT_TOKENS`, `DOCS_LOCAL_LLM_THREADS`, and `DOCS_LOCAL_LLM_BATCH_SIZE`. |
| Boot to healthy (models cached) | **~1 s** | measured on restart with fastembed cache present — the state the **pre-bake** guarantees on any fresh box |

## Resource use (under `/ask`)

| | |
|---|---|
| Mem used | **~1.53 GiB / 1.92 GiB** resident under `/ask` (Qwen + embed + rerank all loaded); swap ~0; `restarts=0`, no OOM |
| Verdict | Fits 2 GB but **tight** (~0.2 GB headroom) with everything resident. Drop `DOCS_RERANK_ENABLED=false` to shed ~300 MB if it OOMs under concurrency. |

## Quality eval — fully local (no hosted judge)

`python local_eval.py` (see [README](./README.md)) — judge-free, nothing leaves the box.
The system is local end-to-end, so it's evaluated locally too. (`ragas_eval.py` with an
OpenAI/gateway judge stays available as an opt-in for LLM-judged faithfulness/relevancy.)

| Date | hit@5 | MRR | keyword coverage | grounding proxy | refusal rate | Notes |
|------|-------|-----|------------------|-----------------|--------------|-------|
| 2026-09-06 | **1.00** (10/10) | 0.83 | 0.90 | 0.82 | **0.50** ⚠️ | baseline: retrieval strong EN+VN; grounding good; **out-of-scope refusal unreliable** — the 0.5B answered "Who won the 2022 World Cup?" instead of declining. |
| 2026-09-06 | 1.00 (10/10) | 0.83 | 0.80 | **0.90** | **1.00** ✅ | after the firmer refusal prompt (`agent.py` `ANSWER_SYSTEM`): **both** out-of-scope questions declined; grounding up 0.82→0.90 (outside knowledge forbidden); retrieval unchanged. Coverage dip 0.90→0.80 is a `must_contain` exact-match artifact, not a regression. |
| 2026-09-10 | 1.00 (10/10) | 0.81 | 0.90 | 0.82 | 1.00 (OOS) ✅ | RAGAS + probe audit (UAT). The dataset scores perfect but is **all keyword-rich queries**, so it can't see the reported bug. **Bare/colloquial queries falsely refuse** (`persona là gì vậy?` → "I don't know" while a correct source card shows): root-caused to `RETRIEVE_TOP_N=20` starving the reranker (the definition chunk sits at vector-rank 21–50). Fix `RETRIEVE_TOP_N` 20→50; live re-test **9/9**, OOS refusal intact. See [`docs/docs-rag-uat-investigation-2026-09-10.md`](../docs/docs-rag-uat-investigation-2026-09-10.md). |

## History

| Date | Event | Result |
|------|-------|--------|
| 2026-09-06 | First successful live UAT deploy + smoke | 928 chunks; `/ask "What is CIR?"` → correct grounded answer, 15.3 s cold; retrieval EN + VN OK |
| 2026-09-06 | Root-caused ~15 min first-boot | ~10 min reranker download (unauth HF) → **pre-bake models in image** |
| 2026-09-06 | Redeploy on merged main + latency run | boot 1 s (cache warm); `/ask` warm 17–42 s (0.5B on 1 vCPU); `/search` ~3 s; mem 1.53/1.92 GiB, no OOM. Note: a concurrent CD deploy (docs-search is in the default set) collided with the manual redeploy — settled healthy. |
| 2026-09-06 | **Pre-bake verified** on the pre-baked image (`sha-ec0479f`, `FASTEMBED_CACHE=/app/model-cache/fastembed`) | **cold restart boot-to-healthy = 22 s** (977 chunks), down from ~15 min — the reranker download is baked away. |
| 2026-09-06 | CD `docs-search` failed (exit 255) | single SSH session idle-dropped ~6 min into enrich from the CI runner → added SSH keepalive (`ServerAliveInterval`). |
| 2026-09-10 | Local CPU tuning validation | warm `/ask` improved from **7.98 s** to **7.34 s** (~8%) with `DOCS_LOCAL_LLM_THREADS=2`, `DOCS_LOCAL_LLM_BATCH_SIZE=512`, and `use_mmap=True`; health remained healthy with no OOM/restarts. |
| 2026-09-10 | Root-caused "shows a source but answers *I don't know*" (bare VN query `persona là gì vậy?`) | The reranker was **starved**: `RETRIEVE_TOP_N=20` cut the pool before the definition chunk (vector-rank 21–50) could be reranked. Fix `RETRIEVE_TOP_N` 20→50 (generator `top_k` unchanged); live UAT re-test **9/9**, out-of-scope refusal preserved. Full write-up in `docs/docs-rag-uat-investigation-2026-09-10.md`. |
