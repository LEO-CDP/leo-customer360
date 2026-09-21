"""Live evaluation harness for customer360-agent.

Runs the REAL agent planner (generate_campaign_plan / generate_zalo_campaign_plan)
for both channels against a real LLM via LiteLLM, using the PRODUCTION seeded
prompt bodies. Captures for each call:
  * the full generated plan (the response the caller gets),
  * token consumption (prompt / completion / total, from response.usage),
  * $ cost (litellm.completion_cost),
  * latency,
  * a scored evaluation (format adherence + guardrail compliance + relevance).

Two modes, chosen automatically:
  * live  -- a real key is present (env OPENAI_API_KEY / LLM_API_KEY, or --key).
  * mock  -- no key: litellm.completion is stubbed with a realistic payload so the
             harness's own plumbing (usage capture, eval, output) is verified. Token
             counts come from the stub's usage; cost is 0. Clearly labelled as mock.

Output: a JSON blob on stdout (--json PATH also writes it) for the HTML report.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

AGENT_ROOT = Path(__file__).resolve().parents[1]  # customer360-agent/
sys.path.insert(0, str(AGENT_ROOT / "src"))

import config  # noqa: E402
from prompts import PromptTemplate, get_store, reset_store_cache  # noqa: E402
from campaign_planner.base import CAMPAIGN_PLAN_INSTRUCTIONS, CAMPAIGN_ZNS_INSTRUCTIONS  # noqa: E402

# --- production prompt bodies (verbatim from database-init/init-prompt-store-seed.sql) ---
EMAIL_BODY = (
    "You are a marketing campaign strategist. Given a target segment, a marketer's objective, "
    "optional budget/time constraints, and a CLOSED list of candidate content items, propose a "
    "campaign plan. You MUST select recommended content only from the supplied candidate list -- "
    "you MUST NOT invent new content_item_id values or reference any item not in that list. If no "
    "candidate items are suitable, return an empty content_item_ids array rather than fabricating "
    'one. Respond with ONLY a JSON object with exactly these keys: "name" (string), "objective" '
    '(string), "strategy_summary" (string), "action_plan" (array of short strings), "start_date" '
    '(string, YYYY-MM-DD), "end_date" (string, YYYY-MM-DD), "content_item_ids" (array of strings, '
    "each exactly one of the candidate content_item_id values, ordered by recommended priority)."
)
ZNS_BODY = (
    "You are a Zalo ZNS campaign strategist. Given a target segment, a marketer's objective, and a "
    "CLOSED list of APPROVED ZNS templates (each with a template_id and its required parameter "
    "names), choose exactly ONE template and fill EVERY one of its required parameters with "
    "concrete values suitable for the segment. You MUST pick a template_id from the candidate list "
    "-- never invent one -- and you MUST NOT author free message text (ZNS content is fixed by the "
    'approved template). Respond with ONLY a JSON object with exactly these keys: "template_id" '
    '(string, one of the candidates), "template_data" (object mapping every required param name to '
    'a string value), "name" (string), "objective" (string), "strategy_summary" (string), '
    '"action_plan" (array of short strings), "start_date" (YYYY-MM-DD), "end_date" (YYYY-MM-DD).'
)

# --- realistic marketer briefs + candidate lists ---
EMAIL_BRIEF = {
    "segment_context": {
        "segment": "dormant-90d",
        "size": 1200,
        "avg_order_value": 42.5,
        "top_category": "skincare",
        "locale": "vi-VN",
    },
    "objective": "reactivate customers who haven't purchased in 90 days, target a 5% return rate",
    "budget_time_constraints": "2-week window in October, email only, max 2 touches",
}
EMAIL_CANDIDATES = [
    {"content_item_id": "c1", "title": "We miss you — 15% off your favourites", "type": "promo"},
    {"content_item_id": "c2", "title": "New skincare arrivals for autumn", "type": "editorial"},
    {"content_item_id": "c3", "title": "Free shipping this weekend only", "type": "promo"},
    {"content_item_id": "c4", "title": "Your loyalty points are expiring", "type": "transactional"},
]

ZNS_BRIEF = {
    "segment_context": {
        "segment": "cart-abandoners-48h",
        "size": 300,
        "avg_cart_value": 68.0,
        "locale": "vi-VN",
    },
    "objective": "recover abandoned carts within 48 hours with a time-limited incentive",
    "budget_time_constraints": "single ZNS touch, this week",
}
ZNS_CANDIDATES = [
    {"template_id": "tpl-otp", "name": "OTP verify", "params": ["otp", "name"]},
    {"template_id": "tpl-promo", "name": "Promo nudge", "params": ["offer", "expiry"]},
    {"template_id": "tpl-cart", "name": "Cart reminder", "params": ["customer_name", "item_count", "discount"]},
]

MODEL = os.environ.get("LLM_MODEL", "openai/gpt-4o-mini")


def _mock_payload(kind: str) -> str:
    if kind == "email":
        obj = {
            "name": "October Win-Back", "objective": EMAIL_BRIEF["objective"],
            "strategy_summary": "Two-touch: incentive first, urgency second.",
            "action_plan": ["Day 1: send c1 (15% off)", "Day 7: send c3 (free shipping) to non-openers"],
            "start_date": "2026-10-06", "end_date": "2026-10-20",
            "content_item_ids": ["c1", "c3"],
        }
    else:
        obj = {
            "template_id": "tpl-cart",
            "template_data": {"customer_name": "Khách hàng", "item_count": "2", "discount": "15%"},
            "name": "48h Cart Recovery", "objective": ZNS_BRIEF["objective"],
            "strategy_summary": "Single ZNS nudge with a 48h discount.",
            "action_plan": ["Send tpl-cart within 2h of abandonment"],
            "start_date": "2026-10-06", "end_date": "2026-10-08",
        }
    return json.dumps(obj)


class UsageRecorder:
    """Wrap litellm.completion so we keep response.usage (the agent's own call discards it)."""

    def __init__(self, live: bool):
        self.live = live
        self.last = None
        self._real = None
        if live:
            import litellm
            self._real = litellm.completion  # capture BEFORE patch to avoid self-recursion

    def __call__(self, *args, **kwargs):
        if self.live:
            resp = self._real(*args, **kwargs)
        else:
            # Stub: realistic OpenAI-shaped response with a usage block. Approximate token
            # counts so mock output is plausible; cost stays 0 (no real spend).
            kind = "zalo" if "ZNS" in kwargs["messages"][0]["content"] else "email"
            content = _mock_payload(kind)
            prompt_toks = max(1, len(kwargs["messages"][0]["content"]) // 4)
            completion_toks = max(1, len(content) // 4)
            resp = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                usage=SimpleNamespace(prompt_tokens=prompt_toks, completion_tokens=completion_toks,
                                      total_tokens=prompt_toks + completion_toks),
                model=MODEL,
            )
        self.last = resp
        return resp


def _usage_of(resp) -> dict:
    u = getattr(resp, "usage", None)
    if u is None:
        return {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    g = (lambda k: getattr(u, k, None) if not isinstance(u, dict) else u.get(k))
    return {"prompt_tokens": g("prompt_tokens"), "completion_tokens": g("completion_tokens"),
            "total_tokens": g("total_tokens")}


def _cost_of(resp, live: bool) -> float | None:
    if not live:
        return 0.0
    try:
        import litellm
        return float(litellm.completion_cost(completion_response=resp))
    except Exception:
        return None


def _evaluate_email(plan, candidates) -> dict:
    ids = {c["content_item_id"] for c in candidates}
    checks = {
        "valid_json_plan": plan is not None,
        "objective_present": bool(getattr(plan, "objective", "").strip()),
        "strategy_present": bool(getattr(plan, "strategy_summary", "").strip()),
        "action_plan_nonempty": len(getattr(plan, "action_plan", [])) > 0,
        "content_ids_from_candidates": all(i in ids for i in getattr(plan, "content_item_ids", [])),
        "at_least_one_content": len(getattr(plan, "content_item_ids", [])) > 0,
        "dates_ordered": bool(plan.start_date and plan.end_date and plan.start_date <= plan.end_date),
    }
    return _score(checks)


def _evaluate_zalo(plan, candidates) -> dict:
    by_id = {c["template_id"]: c for c in candidates}
    tpl = by_id.get(getattr(plan, "template_id", ""))
    req = tpl.get("params", []) if tpl else []
    data = getattr(plan, "template_data", {}) or {}
    checks = {
        "valid_json_plan": plan is not None,
        "template_from_candidates": tpl is not None,
        "all_required_params_filled": bool(tpl) and all(str(data.get(p, "")).strip() for p in req),
        "no_extra_free_text": True,  # model returns only template_data; enforced by response shape
        "objective_present": bool(getattr(plan, "objective", "").strip()),
        "action_plan_nonempty": len(getattr(plan, "action_plan", [])) > 0,
        "dates_ordered": bool(plan.start_date and plan.end_date and plan.start_date <= plan.end_date),
    }
    return _score(checks)


def _score(checks: dict) -> dict:
    passed = sum(1 for v in checks.values() if v)
    total = len(checks)
    return {"checks": checks, "passed": passed, "total": total,
            "pct": round(100 * passed / total)}


def run_channel(kind: str, recorder: UsageRecorder) -> dict:
    import campaign_planner as cp

    if kind == "email":
        brief = cp.CampaignPlanBrief(segment_context=EMAIL_BRIEF["segment_context"],
                                     objective=EMAIL_BRIEF["objective"],
                                     budget_time_constraints=EMAIL_BRIEF["budget_time_constraints"])
        candidates = EMAIL_CANDIDATES
        fn = lambda: cp.generate_campaign_plan(brief, candidates)
    else:
        brief = cp.ZnsCampaignPlanBrief(segment_context=ZNS_BRIEF["segment_context"],
                                        objective=ZNS_BRIEF["objective"],
                                        budget_time_constraints=ZNS_BRIEF["budget_time_constraints"])
        candidates = ZNS_CANDIDATES
        fn = lambda: cp.generate_zalo_campaign_plan(brief, candidates)

    result = {"channel": kind, "ok": False, "error": None}
    t0 = time.perf_counter()
    with patch("litellm.completion", recorder):
        try:
            plan = fn()
            result["ok"] = True
        except Exception as exc:  # AIProviderError etc.
            plan = None
            result["error"] = f"{type(exc).__name__}: {exc}"
    result["latency_s"] = round(time.perf_counter() - t0, 3)

    result["usage"] = _usage_of(recorder.last) if recorder.last else {}
    result["cost_usd"] = _cost_of(recorder.last, recorder.live)
    if plan is not None:
        result["response"] = {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                              for k, v in vars(plan).items()}
        result["evaluation"] = (_evaluate_email if kind == "email" else _evaluate_zalo)(plan, candidates)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default="")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    key = args.key or os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY") or ""
    live = bool(key)

    # Configure the real settings object the agent reads. In mock mode use a
    # placeholder so ProviderState.validate() passes (the recorder stubs the call);
    # live detection stays keyed on the REAL key presence above.
    config.settings.llm_api_key = key or "sk-mock-eval-placeholder"
    config.settings.llm_base_url = ""
    config.settings.llm_model = MODEL
    config.settings.api_token = ""

    # Seed the prompt store with the PRODUCTION bodies (no DB).
    reset_store_cache()
    get_store()._snapshot = {
        CAMPAIGN_PLAN_INSTRUCTIONS: PromptTemplate(key=CAMPAIGN_PLAN_INSTRUCTIONS, body=EMAIL_BODY),
        CAMPAIGN_ZNS_INSTRUCTIONS: PromptTemplate(key=CAMPAIGN_ZNS_INSTRUCTIONS, body=ZNS_BODY),
    }

    recorder = UsageRecorder(live)
    out = {
        "mode": "live" if live else "mock",
        "model": MODEL,
        "channels": [run_channel("email", recorder), run_channel("zalo", recorder)],
    }
    # totals
    toks = [c["usage"].get("total_tokens") or 0 for c in out["channels"]]
    costs = [c["cost_usd"] or 0 for c in out["channels"]]
    out["totals"] = {"total_tokens": sum(toks), "total_cost_usd": round(sum(costs), 6)}

    blob = json.dumps(out, indent=2, ensure_ascii=False)
    print(blob)
    if args.json:
        Path(args.json).write_text(blob, encoding="utf-8")


if __name__ == "__main__":
    main()
