"""Fully-local evaluation of docs-vector-search — NO hosted judge, nothing leaves the box.

Hits the running service's /search + /ask and scores with deterministic, judge-free
checks. This is the local-only alternative to ragas_eval.py (which needs an LLM judge).

    export SERVICE_URL=http://localhost:8000     # or an SSH tunnel to the box
    python local_eval.py

Metrics (all computed locally from the service's own outputs):
  - retrieval hit@k / MRR : is an expected source in /search top-k? (the biggest quality driver)
  - keyword coverage      : fraction of a question's `must_contain` terms present in the answer
  - grounding proxy       : fraction of the answer's content words found in the retrieved
                            contexts — a lightweight, judge-free stand-in for "faithfulness"
  - refusal rate          : out-of-scope questions must be declined ("I don't know"), not answered
"""
from __future__ import annotations

import json
import os
import re
import statistics
import sys
from pathlib import Path

import httpx

# Windows consoles default to cp1252, which can't encode Vietnamese output — force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

HERE = Path(__file__).resolve().parent
DATASET = HERE / "dataset.jsonl"
SERVICE_URL = os.getenv("SERVICE_URL", "http://localhost:8000")
TOP_K = int(os.getenv("EVAL_TOP_K", "5"))

REFUSAL_RE = re.compile(
    r"don'?t know|do not know|not (in|present|found|available|contained|mentioned)"
    r"|cannot find|can'?t find|no (information|answer|mention)|unable to|không (biết|có|tìm)",
    re.I,
)
# content words: Latin + Vietnamese letters, >=4 chars (skips stopword-ish short tokens)
WORD_RE = re.compile(r"[a-zA-ZÀ-ỹ]{4,}")


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]


def _as_list(v) -> list[str]:
    if not v:
        return []
    return v if isinstance(v, list) else [v]


def hit_mrr(expect, paths: list[str]):
    exps = [e.lower() for e in _as_list(expect)]
    if not exps:
        return None, None
    for i, p in enumerate(paths, 1):
        if any(e in p.lower() for e in exps):
            return 1, 1.0 / i
    return 0, 0.0


def coverage(answer: str, terms) -> float | None:
    terms = _as_list(terms)
    if not terms:
        return None
    a = answer.lower()
    return sum(1 for t in terms if t.lower() in a) / len(terms)


def grounding(answer: str, contexts: list[str]) -> float | None:
    ctx = " ".join(contexts).lower()
    words = {w for w in WORD_RE.findall(answer.lower())}
    if not words or not ctx:
        return None
    return sum(1 for w in words if w in ctx) / len(words)


def run(dataset: Path = DATASET, service_url: str = SERVICE_URL) -> dict:
    rows = load(dataset)
    hits, mrrs, covs, grnds, refusals = [], [], [], [], []
    per = []
    with httpx.Client(timeout=240.0) as c:
        for i, r in enumerate(rows, 1):
            q = r["question"]
            paths = [h["path"] for h in c.post(f"{service_url}/search", json={"query": q, "top_n": TOP_K}).json().get("hits", [])][:TOP_K]
            d = c.post(f"{service_url}/ask", json={"question": q}).json()
            ans, ctxs = d.get("answer", ""), d.get("contexts", [])
            row = {"q": q, "lang": r.get("lang", "en"), "oos": bool(r.get("out_of_scope"))}
            if r.get("out_of_scope"):
                ref = 1 if REFUSAL_RE.search(ans) else 0
                refusals.append(ref)
                row["refused"] = bool(ref)
            else:
                h, m = hit_mrr(r.get("expect_source"), paths)
                if h is not None:
                    hits.append(h); mrrs.append(m); row["hit@k"] = h; row["mrr"] = round(m, 3)
                cov = coverage(ans, r.get("must_contain"))
                if cov is not None:
                    covs.append(cov); row["coverage"] = round(cov, 3)
                g = grounding(ans, ctxs)
                if g is not None:
                    grnds.append(g); row["grounding"] = round(g, 3)
            per.append(row)
            print(f"  [{i}/{len(rows)}] {q[:56]}")

    def mean(xs):
        return round(statistics.mean(xs), 3) if xs else None

    scores = {
        "retrieval_hit@%d" % TOP_K: mean(hits),
        "retrieval_mrr": mean(mrrs),
        "keyword_coverage": mean(covs),
        "grounding_proxy": mean(grnds),
        "refusal_rate": mean(refusals),
        "n_scored": len(hits),
        "n_oos": len(refusals),
        "n_total": len(rows),
    }
    (HERE / "local_eval_report.json").write_text(
        json.dumps({"scores": scores, "per_question": per}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n" + "=" * 52)
    print(f"LOCAL EVAL — {len(rows)} questions ({len(refusals)} out-of-scope)  [no hosted judge]")
    print("=" * 52)
    for k, v in scores.items():
        print(f"  {k:<20} {v}")
    print("=" * 52)
    print(f"detail: {HERE / 'local_eval_report.json'}")
    return scores


if __name__ == "__main__":
    run()
