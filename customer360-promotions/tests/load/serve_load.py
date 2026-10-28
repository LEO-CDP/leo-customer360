"""Load driver for the ad server's /serve and /events endpoints (quickstart Scenario 10).

It runs two phases against a server you point it at and reports the latency added
by personalisation, plus how the event intake behaved:

    baseline      /serve with no visitor id (default ranking: no personalisation work)
    personalised  /serve with a visitor id drawn from a pool, then the events a widget
                  would send for what it was shown (impression, and sometimes a click)

The added latency is the personalised phase's percentiles minus the baseline's.
The target (SC-003) is that at least 95% of /serve calls are no more than 50 ms
slower than the same calls without personalisation, and that all of them return a
valid result.

Use a server with personalisation switched ON for the tenant (and a hold-out of 0
so the pool of visitors is not held out), run against a disposable database, and
never against production data. No dependency beyond the HTTP client the tests use.

    python tests/load/serve_load.py --base-url http://localhost:9009 --tenant demo \\
        --placement demo-personalisation-top --rps 200 --duration 30 --visitors 500
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

try:  # the tests' HTTP client is installed as httpx2 in newer environments
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

TARGET_ADDED_MS = 50.0
TARGET_SHARE = 0.95


def percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        return float("nan")
    index = min(int(round(fraction * (len(sorted_values) - 1))), len(sorted_values) - 1)
    return sorted_values[index]


class Phase:
    def __init__(self, name: str) -> None:
        self.name = name
        self.latencies_ms: list[float] = []
        self.statuses: Counter = Counter()
        self.invalid = 0
        self.groups: Counter = Counter()
        self.event_statuses: Counter = Counter()
        self.retry_after_seen = 0
        self._lock = threading.Lock()

    def record_serve(self, elapsed_ms: float, status: int, body: dict | None) -> None:
        valid = (
            status == 200
            and isinstance(body, dict)
            and "request_id" in body
            and isinstance(body.get("ads"), list)
            and "personalisation" in body
        )
        with self._lock:
            self.latencies_ms.append(elapsed_ms)
            self.statuses[status] += 1
            if not valid:
                self.invalid += 1
            elif body:
                self.groups[body["personalisation"].get("group", "?")] += 1

    def record_event(self, status: int, retry_after: str | None) -> None:
        with self._lock:
            self.event_statuses[status] += 1
            if retry_after:
                self.retry_after_seen += 1

    def summary(self) -> dict:
        ordered = sorted(self.latencies_ms)
        return {
            "phase": self.name,
            "serve_calls": len(ordered),
            "p50_ms": round(percentile(ordered, 0.50), 2),
            "p95_ms": round(percentile(ordered, 0.95), 2),
            "p99_ms": round(percentile(ordered, 0.99), 2),
            "mean_ms": round(statistics.fmean(ordered), 2) if ordered else None,
            "serve_statuses": dict(self.statuses),
            "invalid_responses": self.invalid,
            "groups": dict(self.groups),
            "event_statuses": dict(self.event_statuses),
            "retry_after_headers": self.retry_after_seen,
        }


def one_request(client: httpx.Client, args, phase: Phase, personalised: bool, rng: random.Random) -> None:
    params = {"tenant": args.tenant, "limit": args.limit}
    if personalised:
        params["leovid"] = f"load-visitor-{rng.randrange(args.visitors)}"
        params["consent"] = "granted"
    started = time.perf_counter()
    try:
        response = client.get(f"/serve/{args.placement}", params=params)
        elapsed = (time.perf_counter() - started) * 1000
        body = response.json() if response.status_code == 200 else None
        phase.record_serve(elapsed, response.status_code, body)
    except Exception:  # noqa: BLE001 - a failed call is a result, not a crash
        phase.record_serve((time.perf_counter() - started) * 1000, 0, None)
        return

    if not personalised or not body or not body.get("ads"):
        return
    request_id = body["request_id"]
    now = datetime.now(timezone.utc).isoformat()
    events = []
    for ad in body["ads"][: args.events_per_request]:
        events.append({"event_type": "impression", "request_id": request_id, "ad_key": ad["adId"], "event_time": now})
        if rng.random() < args.click_rate:
            events.append({"event_type": "click", "request_id": request_id, "ad_key": ad["adId"], "event_time": now})
    try:
        post = client.post(
            "/events",
            json={"tenant": args.tenant, "leovid": params["leovid"], "events": events[:50]},
        )
        phase.record_event(post.status_code, post.headers.get("Retry-After"))
    except Exception:  # noqa: BLE001
        phase.record_event(0, None)


def run_phase(args, name: str, personalised: bool) -> Phase:
    phase = Phase(name)
    interval = 1.0 / args.rps
    deadline = time.perf_counter() + args.duration
    with httpx.Client(base_url=args.base_url, timeout=args.timeout) as client:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            next_at = time.perf_counter()
            counter = 0
            while next_at < deadline:
                delay = next_at - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                counter += 1
                pool.submit(one_request, client, args, phase, personalised, random.Random(counter))
                next_at += interval
    return phase


def verdict(baseline: Phase, personalised: Phase) -> dict:
    base = sorted(baseline.latencies_ms)
    pers = sorted(personalised.latencies_ms)
    base_median = percentile(base, 0.5)
    within = sum(1 for value in pers if value - base_median <= TARGET_ADDED_MS)
    share = within / len(pers) if pers else 0.0
    all_valid = personalised.invalid == 0 and baseline.invalid == 0 and bool(pers)
    return {
        "added_p50_ms": round(percentile(pers, 0.5) - base_median, 2),
        "added_p95_ms": round(percentile(pers, 0.95) - percentile(base, 0.95), 2),
        "share_within_target": round(share, 4),
        "target": f">= {TARGET_SHARE:.0%} of calls no more than {TARGET_ADDED_MS:.0f} ms slower than the baseline median",
        "all_responses_valid": all_valid,
        "passed": share >= TARGET_SHARE and all_valid,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:9009")
    parser.add_argument("--tenant", default="demo")
    parser.add_argument("--placement", default="demo-personalisation-top")
    parser.add_argument("--rps", type=float, default=50.0, help="serve requests per second, per phase")
    parser.add_argument("--duration", type=float, default=20.0, help="seconds per phase")
    parser.add_argument("--visitors", type=int, default=500, help="size of the visitor pool")
    parser.add_argument("--limit", type=int, default=5, help="ads requested per serve call")
    parser.add_argument("--events-per-request", type=int, default=3)
    parser.add_argument("--click-rate", type=float, default=0.1)
    parser.add_argument("--workers", type=int, default=64)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--json", action="store_true", help="print only the JSON report")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.rps <= 0 or args.duration <= 0 or args.visitors < 1:
        print("rps, duration and visitors must be positive", file=sys.stderr)
        return 2

    baseline = run_phase(args, "baseline (no visitor id)", personalised=False)
    personalised = run_phase(args, "personalised (visitor pool + events)", personalised=True)
    report = {
        "settings": {
            k: getattr(args, k)
            for k in ("base_url", "tenant", "placement", "rps", "duration", "visitors", "events_per_request")
        },
        "phases": [baseline.summary(), personalised.summary()],
        "verdict": verdict(baseline, personalised),
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for phase in report["phases"]:
            print(f"\n{phase['phase']}")
            print(f"  serve calls {phase['serve_calls']}  p50 {phase['p50_ms']} ms  p95 {phase['p95_ms']} ms  p99 {phase['p99_ms']} ms")
            print(f"  statuses {phase['serve_statuses']}  invalid {phase['invalid_responses']}  groups {phase['groups']}")
            if phase["event_statuses"]:
                print(f"  /events statuses {phase['event_statuses']}  Retry-After headers {phase['retry_after_headers']}")
        v = report["verdict"]
        print(f"\nadded latency: p50 {v['added_p50_ms']} ms, p95 {v['added_p95_ms']} ms")
        print(f"within target: {v['share_within_target']:.1%} ({v['target']})")
        print("RESULT:", "PASS" if v["passed"] else "FAIL")
    return 0 if report["verdict"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
