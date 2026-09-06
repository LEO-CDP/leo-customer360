# docs-vector-search — evaluation

Two ways to evaluate the running service. The service is **local end-to-end**, so the
default eval is local too.

Point either at a running instance (SSH-tunnel the UAT box — its `:8000` is private):
```bash
ssh -i ~/.ssh/c360-api_ed25519 -L 8099:localhost:8000 leocdp360@<docs-box-ip>
export SERVICE_URL=http://localhost:8099
```

## 1. `local_eval.py` — fully local, no hosted judge (default)

Judge-free, deterministic, **nothing leaves the box**. Hits `/search` + `/ask` and scores:

| Metric | What / how |
|---|---|
| `retrieval hit@k` / `MRR` | is an expected source in `/search` top-k? (the biggest quality driver) |
| `keyword_coverage` | fraction of a question's `must_contain` terms present in the answer |
| `grounding_proxy` | fraction of the answer's content words found in the retrieved contexts — a judge-free stand-in for "faithfulness" |
| `refusal_rate` | out-of-scope questions must be declined ("I don't know") |

```bash
pip install httpx                 # that's the only dep
python local_eval.py              # prints a table, writes local_eval_report.json
```

Labels live in `dataset.jsonl` (`expect_source`, `must_contain`, `out_of_scope`). Expand it
with more questions drawn from `docs/` to sharpen the signal.

## 2. `ragas_eval.py` — LLM-judged metrics (opt-in, needs a judge)

[RAGAS](https://docs.ragas.io) `faithfulness` / `answer_relevancy` / `context_precision` /
`context_recall` / `answer_correctness` — these need a **capable judge LLM**. The local Qwen
0.5B can't grade itself, so this path uses a **stronger judge**: OpenAI by default, or any
**local** capable model via an OpenAI-compatible endpoint (e.g. Ollama `qwen2.5:7b` →
`OPENAI_BASE_URL=http://localhost:11434/v1`). ⚠️ It sends each answer + retrieved context to
that judge for grading.

```bash
pip install -r requirements-test.txt
export LEO_OPENAI_API_KEY=sk-...            # or OPENAI_API_KEY; + OPENAI_BASE_URL for a gateway
export RAGAS_LLM_MODEL=gpt-4o-mini          # or your judge model
python ragas_eval.py                        # or: pytest -v -s
```

## Files
- `local_eval.py` — fully-local eval (default).
- `ragas_eval.py` / `test_ragas.py` — RAGAS (opt-in judge).
- `dataset.jsonl` — shared question set (+ local-eval labels + optional `ground_truth`).
- `requirements-test.txt` — RAGAS + judge deps (local_eval needs only `httpx`).
- Latest scores: [`PERFORMANCE.md`](./PERFORMANCE.md).
