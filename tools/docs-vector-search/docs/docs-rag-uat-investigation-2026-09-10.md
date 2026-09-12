# Docs RAG — UAT investigation & fix: "shows the right source but answers *I don't know*"

**Date:** 2026-09-10 · **Env:** UAT (`beta.leocdp.com/docs-ai` → docs box `10.100.1.7:8001`)
**Service:** `docs-vector-search` — pgvector + `paraphrase-multilingual-MiniLM-L12-v2` embed + `bge-reranker-base` rerank + `Qwen2.5-0.5B-Instruct` generate · **1017 chunks**

---

## TL;DR

- **Reported:** the Admin-UI chatbot shows a correct source card ("Persona as a Vector — The Persona Attractor") for **`persona là gì vậy ?`** yet replies **"I don't know — that isn't in the documentation."**
- **Reproduced** on UAT, exactly.
- **Root cause:** `RETRIEVE_TOP_N = 20` is **too small**. For short / low-signal queries — especially bare colloquial Vietnamese — the *definitional* persona chunks sit at vector-rank **21–50**, so they never reach the reranker. The 20 survivors all rerank as junk (~ −10), the generator gets no real definition in its top-5 context, and the 0.5B model correctly-but-unhelpfully refuses.
- **Fix (config-only):** `RETRIEVE_TOP_N` **20 → 50**. The reranker then finds the right chunks and ranks them **first** (rerank score **+1.34**). Generator `top_k` (what the model reads) is unchanged, so latency/memory barely move.
- **Validated live** (via per-request `top_n=50`, no redeploy): the exact failing query now answers, all 7 short-query probes answer, and both out-of-scope questions still refuse → **9/9**.
- **Not a prompt bug.** The over-refusal was a *symptom* of thin retrieval; the refusal prompt was left untouched, so out-of-scope refusal is preserved.

---

## 1. The report & reproduction

The user's Admin-UI transcript: question **`persona là gì vậy ?`** ("so what is persona?"), a correct-looking source list, but the answer *"I don't know — that isn't in the documentation."*

Reproduced against UAT (`POST /docs-ai/ask`, proper UTF-8 body):

```
ANSWER: "I don't know — that isn't in the documentation."
SOURCES (5):
  - Persona as a Vector — 5. The Persona Attractor         ← only real persona hit (a deep §5)
  - Customer 360 — DevOps Quick Reference — LEO_GOOGLE_GENAI_API_KEY …   ← junk
  - Customer 360 — DevOps Quick Reference — Output {"status":"ok"}       ← junk
  - Customer 360 — Production Deployment … apiBase "/api/v1"             ← junk
  - Customer 360 — Production Deployment … status ok                     ← junk
```

The UI shows *sources* (retrieval ran) but 4 of the 5 are unrelated DevOps/deploy chunks, and the one persona chunk (§5 "The Persona Attractor") is an abstract dynamical-systems section — it contains no plain "a persona is …" sentence. So the generator has nothing to ground a definition on and refuses.

---

## 2. Method

The service `:8001` is internal-only; it is reachable publicly through Caddy at `https://beta.leocdp.com/docs-ai/*` (`handle_path` strips the prefix → `/ask`, `/search`). Everything below hits that route (paced for the 10 req/60 s per-IP rate limit).

Three lenses, all against the **live UAT** service:

1. **RAGAS** (the prepared harness in `test/`, `.venv` already has ragas 0.2.15) — LLM-judged faithfulness / relevancy / context precision+recall, run with a **fully-local judge** (Ollama `qwen2.5:3b` + `nomic-embed-text` via the OpenAI-compatible endpoint — nothing leaves the box). Run **offline** over collected answers so UAT is hit only ~12 times.
2. **Judge-free eval** (`local_eval.py` metrics) — retrieval hit@k / MRR, keyword coverage, grounding proxy, refusal rate. Deterministic.
3. **Targeted probes** — the actual failing query plus near-variants, and a `top_n` retrieval sweep to locate the missing chunk. This is what caught the bug.

---

## 3. Finding 1 — the prepared dataset passes perfectly, so it never showed the bug

Judge-free metrics over `test/dataset.jsonl` (10 in-scope + 2 out-of-scope), baseline (`top_n=20`):

| Metric | Score | |
|---|---|---|
| retrieval hit@5 | **1.00** (10/10) | every expected source retrieved |
| retrieval MRR | 0.808 | |
| keyword coverage | 0.90 | |
| grounding proxy | 0.817 | |
| **in-scope refusal** | **0.00** (0/10) | *nothing refused* |
| out-of-scope refusal | 1.00 (2/2) | correctly declined |

Every dataset question was **answered**, including `What is 'Persona as a Vector'?` and the Vietnamese `Persona được biểu diễn như thế nào trong marketing 8.0?`. The dataset's questions are "leading" — they embed doc keywords (`Persona as a Vector`, `marketing 8.0`, `Customer Identity Resolution`), which pull the right chunks. **The prepared set cannot reproduce the reported failure**, which is exactly why the bug shipped.

---

## 4. Finding 2 — the failure is retrieval-recall on short / colloquial queries

Probing the real question and near-variants (baseline `top_n=20`):

| Query | Result |
|---|---|
| `persona là gì vậy ?` | **REFUSED** ← the report |
| `persona là gì?` | answered |
| `persona là gì` | answered |
| `what is persona?` | answered |
| `định nghĩa persona` | answered |
| `giải thích persona là gì` | answered |
| `CIR là gì?` | answered |

One trailing colloquial word (`vậy`) flips the outcome. That razor-sensitivity is the fingerprint of a **retrieval-recall** problem, not a generation-prompt problem — the query barely perturbs but the retrieved set collapses.

### The smoking gun — reranked `/search` for `persona là gì vậy ?`

The generator only ever sees the **top-5**. Widening the first-stage pool from 20 to 50 changes those top-5 completely:

**`top_n=20` (current) — reranked survivors:**

| rank | rerank score | chunk |
|---|---|---|
| 1 | **−7.03** | Persona as a Vector — §5 The Persona Attractor |
| 2 | −10.19 | DevOps Quick Reference — LEO_GOOGLE_GENAI_API_KEY |
| 3 | −10.19 | DevOps Quick Reference — Output {"status":"ok"} |
| 4 | −10.19 | Production Deployment — apiBase "/api/v1" |
| 5 | −10.19 | Production Deployment — status ok |
| … | ~−10.2 | (all remaining: Marketing Dashboard, CIR chunks) |

Every candidate is junk (≈ −10); the definitional persona chunks are **not in the pool at all**.

**`top_n=50` (fix) — reranked, same query:**

| rank | rerank score | chunk |
|---|---|---|
| 1 | **+1.34** | Persona as a Vector — **§2.1 Jung: Persona, Self, and Individuation** |
| 2 | **+0.36** | Persona as a Vector — **§1. Introduction** |
| 3 | −0.98 | Persona as a Vector — §4. Current & Desired Persona |
| 5 | −3.05 | Customer Persona Resolution — Abstract |
| 6 | −3.56 | Persona as a Vector — **§3.1 Persona Is a Dynamic State** |
| 9 | −4.41 | Persona as a Vector — Abstract |

The §2.1 Jung chunk — *"Persona is the social face through which an individual interacts with the external world"* — is the actual definition, and it reranks **#1 with a positive score**. It was simply sitting at vector-rank 21–50, unreachable to the reranker at `top_n=20`.

**Why 20 was fatal:** the reranker can only reorder what first-stage vector search hands it; it cannot rescue a relevant chunk ranked 21–50. For low-signal queries the MiniLM embedding scatters the right chunks past position 20 while generic "central" chunks (DevOps quick-ref, deploy, dashboard) fill the top-20.

---

## 5. Root cause

> `RETRIEVE_TOP_N = 20` starves the cross-encoder reranker. On short / low-signal queries (bare colloquial Vietnamese being the worst case) the definitional chunk lands at vector-rank 21–50 and never enters the rerank pool; the generator's top-5 context is left with only unrelated chunks, and the 0.5B model refuses. The refusal is *correct behaviour on bad input* — the defect is upstream, in recall.

---

## 6. The fix

`tools/docs-vector-search/src/config.py` (+ `.env.example`):

```diff
-RETRIEVE_TOP_N = int(os.getenv("RETRIEVE_TOP_N", "20"))
+RETRIEVE_TOP_N = int(os.getenv("RETRIEVE_TOP_N", "50"))
```

Low-risk by construction:

- **Generation is unchanged** — `RERANK_TOP_K = 5` and the 6000-char context budget are untouched, so the 0.5B still reads 5 chunks. No extra generation cost or RAM.
- **Only the reranker sees more** — a cross-encoder pass over 50 short passages instead of 20 (a few hundred ms of CPU). Measured `/ask` latency moved ~33 s → ~37 s.
- **Within existing caps** — `TOP_N_MAX` is already 50, so no cap change and no new attack surface.
- **Refusal prompt untouched** — out-of-scope refusal (the behaviour hardened earlier, per `PERFORMANCE.md`) is preserved.

---

## 7. Before / after validation (live, `top_n=50` per request)

| Query | expect | `top_n=20` | `top_n=50` (fix) |
|---|---|---|---|
| `persona là gì vậy ?` | answer | **refuse** ❌ | answer ✅ |
| `persona là gì?` | answer | answer ✅ | answer ✅ |
| `persona là gì` | answer | answer ✅ | answer ✅ |
| `what is persona?` | answer | answer ✅ | answer ✅ |
| `định nghĩa persona` | answer | answer ✅ | answer ✅ |
| `giải thích persona là gì` | answer | answer ✅ | answer ✅ |
| `CIR là gì?` | answer | answer ✅ | answer ✅ |
| `What is the weather in Hanoi today?` | refuse | refuse ✅ | refuse ✅ |
| `Who won the 2022 FIFA World Cup?` | refuse | refuse ✅ | refuse ✅ |

**9/9 correct at `top_n=50`** — the reported query is fixed and out-of-scope refusal does not regress.

The fixed query now returns the right sources and an answer:

```
Q: persona là gì vậy ?   (top_n=50)
ANSWER: "persona là một mô hình nhân vật trong một hệ thống mô hình nhân vật."
SOURCES: Persona as a Vector — §2.1 Jung · §1 Introduction · §4 Current & Desired Persona · …
```

(The answer is grounded but terse — that ceiling is the 0.5B generator, a separate quality axis from this retrieval bug. See §9.)

---

## 8. RAGAS baseline (local judge)

The prepared harness (`test/ragas_eval.py`, ragas 0.2.15 in `.venv`) was run over the collected UAT answers. No OpenAI / gateway judge key is configured on this machine, so per the README's option 2 the judge was **fully local** — Ollama `qwen2.5:3b` + `nomic-embed-text` via the OpenAI-compatible endpoint (nothing leaves the box). One Ollama fix was needed: `OpenAIEmbeddings(check_embedding_ctx_length=False)`, else it sends tokenized int arrays that Ollama's `/v1/embeddings` rejects.

| RAGAS metric | mean | rows scored |
|---|---|---|
| faithfulness | 0.50 | **2 / 12** |
| answer_relevancy | 0.61 | **1 / 12** |
| context_precision (w/ reference) | — | 0 / 2 (NaN) |
| context_recall | — | 0 / 2 (NaN) |
| answer_correctness | — | 0 / 2 (NaN) |

**Read this result with care: the local judge is too weak for RAGAS, not the service.** `qwen2.5:3b` returned **NaN on 10 of 12 rows** — it can't reliably emit the structured statement-extraction / NLI JSON RAGAS requires — so the aggregates are not trustworthy signal. A serial pass with the larger local `gemma4` scored **even worse — 0/12 rows parsed**, confirming the limitation is local-judge capability, not concurrency. RAGAS-proper needs a **capable judge**; the harness defaults to `gpt-4o-mini` for exactly this reason.

**So the conclusive evidence for this bug is the judge-independent work in §3–§4** — the deterministic judge-free metrics and the `top_n` retrieval sweep, which need no judge and precisely localize the fault. To get trustworthy RAGAS numbers later, point the prepared harness at a real judge:

```bash
export SERVICE_URL=https://beta.leocdp.com/docs-ai
export LEO_OPENAI_API_KEY=sk-...            # or your gateway key + OPENAI_BASE_URL
export RAGAS_LLM_MODEL=gpt-4o-mini
cd tools/docs-vector-search/test && python ragas_eval.py
```

Raw per-row scores: `ragas_tier1.csv`, `ragas_report.json`.

---

## 9. Recommendations / next steps

1. **Deploy the fix to UAT** — redeploy `docs-vector-search` (`deployments/server/deploy-docs-search.sh uat`, or the CD `docs-search` step). The code change is config-only; no re-`enrich` needed. Until redeploy, the default remains 20. *(The live validation above used per-request `top_n=50`, which the API already accepts up to `TOP_N_MAX=50`.)*
2. **Add the real failure mode to the eval set** — the current `dataset.jsonl` is all keyword-rich "leading" queries and scores a perfect 1.0, hiding this class of bug. Add bare/colloquial queries as regression guards, e.g. `persona là gì vậy ?`, `persona là gì`, `what is persona?`, `định nghĩa persona`, `CIR là gì?` (label `must_contain: ["persona"]` / `["identity"]`). These would have caught the regression.
3. **Generation quality (separate track).** With good retrieval the 0.5B still gives thin/tautological Vietnamese answers ("persona là một mô hình nhân vật"). If answer quality matters, that's the generator, not retrieval — options: a larger local model, or the OpenAI provider seam (`DOCS_LLM_PROVIDER=openai`, `DOCS_OPENAI_LLM_MODEL=...`) already in `providers.py`. Out of scope for this fix.
4. **Optional:** consider `RETRIEVE_TOP_N=40` if the extra rerank latency ever matters — 40 also covers this case with margin — but 50 = `TOP_N_MAX` is the safe default.

---

## Appendix — reproduce

```bash
# 1. Reproduce the bug (proper UTF-8 body required)
python -c "import json;open('q.json','w',encoding='utf-8').write(json.dumps({'question':'persona là gì vậy ?'},ensure_ascii=False))"
curl -s -X POST https://beta.leocdp.com/docs-ai/ask \
  -H 'content-type: application/json; charset=utf-8' --data-binary @q.json

# 2. See the missing chunk appear only with a wider pool
curl -s -X POST https://beta.leocdp.com/docs-ai/search \
  -H 'content-type: application/json' --data-binary '{"query":"persona là gì vậy ?","top_n":20}'   # all junk
curl -s -X POST https://beta.leocdp.com/docs-ai/search \
  -H 'content-type: application/json' --data-binary '{"query":"persona là gì vậy ?","top_n":50}'   # §2.1 Jung ranks #1

# 3. Confirm the fix behaviour without redeploy
curl -s -X POST https://beta.leocdp.com/docs-ai/ask \
  -H 'content-type: application/json' --data-binary '{"question":"persona là gì vậy ?","top_n":50}'
```

Evidence bundle in this folder: `metrics.json` (judge-free + before/after), `ragas_report.json`, `ragas_tier1.csv`, `ragas_tier2.csv`, `samples.json` (raw UAT answers).
