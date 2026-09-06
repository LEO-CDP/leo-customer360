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
| `POST /search` | ~1–3 s | embed query + pgvector top-N + bge rerank (CPU) |
| `POST /ask` (first call) | **15.3 s** | includes lazy Qwen load; grounded answer + 5 sources |
| `POST /ask` (warm) | _TBD_ | re-measure once Qwen is resident |

## Resource use (under `/ask`)

| | |
|---|---|
| Mem used | ~1.14 GB / 1.97 GB (≈0.83 GB free); swap ~0 |
| Verdict | Fits 2 GB with headroom while reranker on. Drop `DOCS_RERANK_ENABLED=false` to shed ~300 MB if it ever OOMs. |

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
