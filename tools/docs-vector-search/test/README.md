# docs-vector-search — RAGAS evaluation

A **real** RAG evaluation: it calls the running service's `POST /ask` for each question,
captures the answer **and the exact contexts the generator saw** (the `/ask` response now
returns `contexts`), then scores the pipeline with [RAGAS](https://docs.ragas.io).

## Why a hosted judge for a local service

The service is **fully local** (Qwen 0.5B generate, e5 embed, bge rerank). RAGAS metrics
need a **strong judge model** to grade faithfulness/relevancy — a 0.5B model cannot reliably
grade itself. So evaluation uses a stronger **hosted judge** (OpenAI via the repo's
`LEO_OPENAI_API_KEY`). This is standard RAGAS practice: **the eval judge is not the
production model.** Nothing about serving changes — the judge is used only to score.

## Metrics

| Metric | Needs `ground_truth`? | What it measures |
|---|---|---|
| `faithfulness` | no | are the answer's claims grounded in the retrieved contexts (no hallucination) |
| `answer_relevancy` | no | does the answer actually address the question |
| `context_recall` | yes | did retrieval fetch the info the reference answer needs |
| `llm_context_precision_with_reference` | yes | are the retrieved contexts on-point (not noise) |
| `answer_correctness` | yes | answer vs. reference answer |

Rows in `dataset.jsonl` with a `ground_truth` get the reference-based metrics too; rows
without still get faithfulness + answer_relevancy. **Expand `dataset.jsonl`** with more
questions (EN + VN) and verified `ground_truth` answers drawn from `docs/` for a stronger
signal — the seeded references are a starting point to review.

## Run

```bash
cd tools/docs-vector-search/test
pip install -r requirements-test.txt

# 1) Point at a running service. For the UAT box (port 8000 is not public), tunnel:
#      ssh -i ~/.ssh/c360-api_ed25519 -L 8000:localhost:8000 leocdp360@<docs-box-ip>
export SERVICE_URL=http://localhost:8000

# 2) Judge creds (reuse the repo's OpenAI secret)
export LEO_OPENAI_API_KEY=sk-...
export RAGAS_LLM_MODEL=gpt-4o-mini            # optional
export RAGAS_EMBED_MODEL=text-embedding-3-small
# export OPENAI_BASE_URL=...                  # optional OpenAI-compatible gateway

# 3a) Ad-hoc: print a score table + write ragas_report.json / ragas_tier1.csv / ragas_tier2.csv
python ragas_eval.py

# 3b) As a test gate (asserts per-metric thresholds; skips if no key/service)
pytest -v -s
```

Thresholds are modest by default (local 0.5B generator). Tune per metric:
`MIN_FAITHFULNESS`, `MIN_ANSWER_RELEVANCY`, `MIN_CONTEXT_RECALL`, `MIN_CONTEXT_PRECISION`.

## Files

- `dataset.jsonl` — questions (+ optional `ground_truth`, `lang`).
- `ragas_eval.py` — query the service → build `EvaluationDataset` → `evaluate` → report.
- `test_ragas.py` — pytest gate over the metric means.
- `requirements-test.txt` — RAGAS + judge deps (kept out of the service runtime image).
