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

## Startup (cold boot)

The server warms embed + rerank in the lifespan; Qwen loads lazily on the first `/ask`.

| Phase | Before model pre-bake | After model pre-bake (baked in image) |
|---|---|---|
| Qwen GGUF fetch (deploy step, curl) | ~6 min first deploy, then host-cached | same (host-cached in `/opt/c360/docs-models`) |
| Reranker download at boot (unauth HF) | **~10 min** (dominant cost) | **0** — baked into the image |
| Embed download at boot | (cached after enrich) | **0** — baked |
| **Time to healthy `:8000`** | **~15 min** (fresh box) | **expected ~1–2 min** (model loads only) |

> The reranker download from **unauthenticated** Hugging Face was the killer (~10 min on the
> box). Fix: pre-bake the default embed + rerank models into the image at build time
> (`Dockerfile` → `FASTEMBED_CACHE=/app/model-cache/fastembed`). Alternative: set `HF_TOKEN`.

## Latency (warm)

Measured 2026-09-06 (UAT, first live smoke), `curl` from the box (`localhost:8000`):

| Endpoint | Latency | Notes |
|---|---|---|
| `GET /health` | <100 ms | chunk count + model names |
| `POST /search` | ~3 s | embed query + pgvector top-N + bge rerank (CPU) |
| `POST /ask` (first call) | ~15–42 s | includes lazy Qwen load; grounded answer + 5 sources |
| `POST /ask` (warm) | **~17–42 s** | measured; **dominated by Qwen 0.5B generation on 1 vCPU** — scales with answer length, not model load. To cut it: lower `GEN_MAX_TOKENS`, tune `GEN_THREADS`, or a bigger box. |
| Boot to healthy (models cached) | **~1 s** | measured on restart with fastembed cache present — the state the **pre-bake** guarantees on any fresh box |

## Resource use (under `/ask`)

| | |
|---|---|
| Mem used | **~1.53 GiB / 1.92 GiB** resident under `/ask` (Qwen + embed + rerank all loaded); swap ~0; `restarts=0`, no OOM |
| Verdict | Fits 2 GB but **tight** (~0.2 GB headroom) with everything resident. Drop `DOCS_RERANK_ENABLED=false` to shed ~300 MB if it OOMs under concurrency. |

## RAGAS scores

Run `python ragas_eval.py` (see [README](./README.md)); record the run here.

| Date | faithfulness | answer_relevancy | context_recall | context_precision | answer_correctness | Notes |
|------|-------------|------------------|----------------|-------------------|--------------------|-------|
| _pending_ | | | | | | first RAGAS run (judge: OpenAI) |

## History

| Date | Event | Result |
|------|-------|--------|
| 2026-09-06 | First successful live UAT deploy + smoke | 928 chunks; `/ask "What is CIR?"` → correct grounded answer, 15.3 s cold; retrieval EN + VN OK |
| 2026-09-06 | Root-caused ~15 min first-boot | ~10 min reranker download (unauth HF) → **pre-bake models in image** |
| 2026-09-06 | Redeploy on merged main + latency run | boot 1 s (cache warm); `/ask` warm 17–42 s (0.5B on 1 vCPU); `/search` ~3 s; mem 1.53/1.92 GiB, no OOM. Note: a concurrent CD deploy (docs-search is in the default set) collided with the manual redeploy — settled healthy. |
