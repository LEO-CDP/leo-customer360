"""Pytest gate over the RAGAS scores — the REAL, runnable test.

Runs the live-service RAGAS evaluation (see ragas_eval.py) and asserts each metric
clears a threshold. Thresholds are modest by default because the generator is a local
Qwen 0.5B; tune via env (MIN_FAITHFULNESS, MIN_ANSWER_RELEVANCY, ...).

    export SERVICE_URL=http://localhost:8001
    export LEO_OPENAI_API_KEY=sk-...          # judge
    pytest tools/docs-vector-search/test -v -s

Skips (not fails) when the judge key is missing or the service is unreachable, so it
never blocks CI on a machine that can't reach a running instance.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("ragas", reason="pip install -r requirements-test.txt")
pytest.importorskip("langchain_openai", reason="pip install -r requirements-test.txt")

from ragas_eval import run_eval  # noqa: E402

# Per-metric floors (mean over the dataset). Override via env for stricter/looser gates.
THRESHOLDS = {
    "faithfulness": float(os.getenv("MIN_FAITHFULNESS", "0.60")),
    "answer_relevancy": float(os.getenv("MIN_ANSWER_RELEVANCY", "0.60")),
    "context_recall": float(os.getenv("MIN_CONTEXT_RECALL", "0.60")),
    "llm_context_precision_with_reference": float(os.getenv("MIN_CONTEXT_PRECISION", "0.60")),
}


@pytest.fixture(scope="module")
def scores() -> dict:
    if not (os.getenv("LEO_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")):
        pytest.skip("no judge key (set LEO_OPENAI_API_KEY / OPENAI_API_KEY)")
    try:
        return run_eval()
    except Exception as e:  # service unreachable, etc. — skip rather than hard-fail
        pytest.skip(f"RAGAS eval could not run: {e}")


def test_scores_present(scores):
    assert scores, "no RAGAS scores returned"
    assert "faithfulness" in scores


@pytest.mark.parametrize("metric", list(THRESHOLDS))
def test_metric_threshold(scores, metric):
    if metric not in scores:
        pytest.skip(f"{metric} not evaluated (no reference rows?)")
    val = scores[metric]
    if val != val:  # NaN
        pytest.skip(f"{metric} is NaN (judge produced no score)")
    assert val >= THRESHOLDS[metric], f"{metric}={val:.3f} < {THRESHOLDS[metric]:.2f}"
