"""RAGAS evaluation of the live docs-vector-search service — a REAL test.

Flow: for each question in dataset.jsonl, call the running service's POST /ask,
collect (question, answer, retrieved_contexts, reference), then score with RAGAS.

    # point at the service (SSH-tunnel the UAT box if needed:
    #   ssh -L 8001:localhost:8001 leocdp360@<docs-box-ip>)
    export SERVICE_URL=http://localhost:8001

    # RAGAS needs a JUDGE model. The service is fully local (Qwen 0.5B) and far too
    # small to grade itself, so evaluation uses a stronger hosted judge — standard
    # RAGAS practice (eval judge != production model). Reuses the repo's OpenAI creds.
    export LEO_OPENAI_API_KEY=sk-...            # or OPENAI_API_KEY
    export RAGAS_LLM_MODEL=gpt-4o-mini          # optional (default below)
    export RAGAS_EMBEDDING_MODEL=text-embedding-3-small
    export OPENAI_BASE_URL=...                  # optional (OpenAI-compatible gateway)

    python ragas_eval.py                        # prints a table, writes ragas_report.{json,csv}

Metrics:
  - faithfulness, answer_relevancy  -> every row (no reference needed)
  - context_precision, context_recall, answer_correctness -> rows with a `ground_truth`
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
DEFAULT_DATASET = HERE / "dataset.jsonl"
SERVICE_URL = os.getenv("SERVICE_URL", "http://localhost:8001")


# --------------------------------------------------------------------------- data
def load_rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            rows.append(json.loads(line))
    return rows


def fetch_samples(rows: list[dict], service_url: str, timeout: float = 180.0) -> list[dict]:
    """Query the REAL service once per question; capture the exact contexts it used."""
    samples = []
    with httpx.Client(timeout=timeout) as client:
        for i, r in enumerate(rows, 1):
            q = r["question"]
            resp = client.post(f"{service_url}/ask", json={"question": q})
            resp.raise_for_status()
            d = resp.json()
            contexts = d.get("contexts")
            if contexts is None:  # older service without contexts in /ask — fall back to /search
                s = client.post(f"{service_url}/search", json={"query": q}).json()
                contexts = [h.get("text", "") for h in s.get("hits", []) if h.get("text")]
            samples.append(
                {
                    "user_input": q,
                    "response": d.get("answer", ""),
                    "retrieved_contexts": [c for c in (contexts or []) if c],
                    "reference": r.get("ground_truth"),
                    "lang": r.get("lang", "en"),
                }
            )
            print(f"  [{i}/{len(rows)}] asked: {q[:60]}")
    return samples


# ------------------------------------------------------------------------- judge
def build_judge():
    """Wrap a hosted judge LLM + embeddings for RAGAS. Raises if no API key."""
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    api_key = os.getenv("LEO_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "No judge API key — set LEO_OPENAI_API_KEY (or OPENAI_API_KEY). RAGAS needs a "
            "strong judge; the local Qwen 0.5B cannot reliably grade itself."
        )
    base_url = os.getenv("OPENAI_BASE_URL") or None
    llm_model = os.getenv("RAGAS_LLM_MODEL", "gpt-4o-mini")
    embed_model = os.getenv("RAGAS_EMBEDDING_MODEL", "text-embedding-3-small")
    llm = LangchainLLMWrapper(
        ChatOpenAI(model=llm_model, api_key=api_key, base_url=base_url, temperature=0)
    )
    embeddings = LangchainEmbeddingsWrapper(
        OpenAIEmbeddings(model=embed_model, api_key=api_key, base_url=base_url)
    )
    return llm, embeddings


def _metrics(with_reference: bool):
    """Metric instances for the current RAGAS. Reference-based ones are added only
    when every scored row carries a ground_truth."""
    from ragas.metrics import Faithfulness, ResponseRelevancy

    metrics = [Faithfulness(), ResponseRelevancy()]
    if with_reference:
        from ragas.metrics import (
            AnswerCorrectness,
            LLMContextPrecisionWithReference,
            LLMContextRecall,
        )

        metrics += [
            LLMContextPrecisionWithReference(),
            LLMContextRecall(),
            AnswerCorrectness(),
        ]
    return metrics


# -------------------------------------------------------------------------- eval
def run_eval(dataset: Path = DEFAULT_DATASET, service_url: str = SERVICE_URL) -> dict:
    """Run the full RAGAS evaluation against the live service. Returns {metric: mean}."""
    from ragas import EvaluationDataset, evaluate

    rows = load_rows(dataset)
    samples = fetch_samples(rows, service_url)
    llm, embeddings = build_judge()

    scores: dict[str, float] = {}
    reports = {}

    # Tier 1 — no reference needed: score every row.
    t1 = EvaluationDataset.from_list(
        [{k: s[k] for k in ("user_input", "response", "retrieved_contexts")} for s in samples]
    )
    r1 = evaluate(t1, metrics=_metrics(False), llm=llm, embeddings=embeddings)
    df1 = r1.to_pandas()
    reports["tier1"] = df1

    # Tier 2 — reference-based metrics on the labeled subset only.
    labeled = [s for s in samples if s.get("reference")]
    df2 = None
    if labeled:
        t2 = EvaluationDataset.from_list(
            [
                {k: s[k] for k in ("user_input", "response", "retrieved_contexts", "reference")}
                for s in labeled
            ]
        )
        r2 = evaluate(t2, metrics=_metrics(True), llm=llm, embeddings=embeddings)
        df2 = r2.to_pandas()
        reports["tier2"] = df2

    # Aggregate per-metric means (ignore NaN).
    import pandas as pd

    metric_cols_1 = [c for c in df1.columns if c not in ("user_input", "response", "retrieved_contexts", "reference")]
    for c in metric_cols_1:
        scores[c] = float(pd.to_numeric(df1[c], errors="coerce").mean())
    if df2 is not None:
        metric_cols_2 = [c for c in df2.columns if c not in ("user_input", "response", "retrieved_contexts", "reference")]
        for c in metric_cols_2:
            if c not in scores:
                scores[c] = float(pd.to_numeric(df2[c], errors="coerce").mean())

    _save_report(scores, reports)
    _print_report(scores, len(samples), len(labeled))
    return scores


def _save_report(scores: dict, reports: dict) -> None:
    (HERE / "ragas_report.json").write_text(
        json.dumps({"scores": scores}, indent=2), encoding="utf-8"
    )
    for name, df in reports.items():
        df.to_csv(HERE / f"ragas_{name}.csv", index=False)


def _print_report(scores: dict, n_total: int, n_labeled: int) -> None:
    print("\n" + "=" * 56)
    print(f"RAGAS report — {n_total} questions ({n_labeled} with reference)")
    print("=" * 56)
    for metric, val in scores.items():
        print(f"  {metric:<28} {val:.3f}")
    print("=" * 56)
    print(f"detail: {HERE/'ragas_tier1.csv'}" + (f", {HERE/'ragas_tier2.csv'}" if n_labeled else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description="RAGAS eval of the live docs-vector-search service.")
    ap.add_argument("--service-url", default=SERVICE_URL)
    ap.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    args = ap.parse_args()
    run_eval(args.dataset, args.service_url)


if __name__ == "__main__":
    main()
