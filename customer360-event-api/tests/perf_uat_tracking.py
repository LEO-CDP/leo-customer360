"""Stepped-RPS performance / load test against the REAL UAT data-tracking endpoint.

Ramps the offered load in RPS steps: start at ``--start-rps`` (10), and while a
step's success rate stays >= ``--success-threshold`` (0.99), step up by
``--step-rps`` (10) and run again — stopping at the first step below the
threshold (or ``--max-rps``). Each step is recorded as its own run.

For every request it sends a dummy tracking event to
``POST {endpoint}/api/v1/tracking/logs`` and ASYNCHRONOUSLY verifies the batch
actually landed in the S3 object store (``HEAD`` on the exact ``bucket`` /
``object_key`` the API returns). A request counts as a SUCCESS only when it is
accepted (2xx) AND found in the store. It logs total time, per-step throughput,
latency percentiles, and every error, and writes a JSON results file.

All events are tagged as PERFORMANCE-TEST DATA so they can be deleted later:
  * they all land in ONE bucket ``data-tracking-<PERF_DATA_SOURCE_ID>`` (the
    single deletion handle), and
  * each event carries ``_perf_test=true`` plus ``_perf_run_id`` / ``_perf_rps``.

Run standalone:
    python tests/perf_uat_tracking.py --start-rps 10 --step-rps 10 --per-step 200

Or via pytest (gated by RUN_UAT_PERF so it never runs in ordinary CI):
    RUN_UAT_PERF=1 pytest tests/perf_uat_tracking.py -s

Config — CLI flags override these env vars:
    TRACKING_ENDPOINT    base URL, default https://beta.leocdp.com/data
    PERF_START_RPS       default 10        PERF_STEP_RPS   default 10
    PERF_MAX_RPS         default 1000      PERF_PER_STEP   default 200
    PERF_SUCCESS_THRESHOLD default 0.99
    PERF_DATA_SOURCE_ID  perf bucket = data-tracking-<id> (deletion handle)
    PERF_VERIFY_STORE    1 (default) = async S3 store-check; 0 = send only
    S3 store-check creds (mirror deployments/server/deploy-tracking.sh):
      S3_ENDPOINT_URL, S3_REGION (default us-east-1),
      S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, S3_FORCE_PATH_STYLE (default true)

Rate-limit note: the service rate-limits on the client IP it *sees*, which
through Caddy -> nginx LB is ONE global bucket of TRACKING_RATE_LIMIT_REQUESTS
per window (default 120 / 60s). A 10-RPS start already exceeds that, so raise
TRACKING_RATE_LIMIT_REQUESTS on the UAT box for the duration of the ramp,
otherwise every step past ~2 RPS is throttled by the limiter, not real capacity.
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("perf-uat-tracking")

DEFAULT_ENDPOINT = "https://beta.leocdp.com/data"
DEFAULT_DATA_SOURCE_ID = "abcdef00-0000-4000-8000-000000000001"  # perf bucket / deletion handle
TRACKING_PATH = "/api/v1/tracking/logs"
# A plain browser UA — the app drops requests whose UA matches a bot pattern.
USER_AGENT = "c360-perf-test/1.0 (+load-test) Mozilla/5.0"


@dataclass
class ItemResult:
    index: int
    http_status: Optional[int] = None
    accepted: bool = False
    stored: bool = False
    throttled: bool = False
    send_ms: float = 0.0
    verify_ms: float = 0.0
    error: Optional[str] = None


@dataclass
class StepReport:
    rps: int
    count: int
    verify_store: bool
    wall_seconds: float = 0.0
    achieved_rps: float = 0.0
    accepted: int = 0
    stored_ok: int = 0
    success: int = 0          # accepted AND (stored, or store-check disabled)
    throttled: int = 0
    failed: int = 0
    success_rate: float = 0.0
    send_latency_ms: dict[str, float] = field(default_factory=dict)
    verify_latency_ms: dict[str, float] = field(default_factory=dict)
    errors: dict[str, int] = field(default_factory=dict)


def _pct(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)

    def q(p: float) -> float:
        return round(ordered[min(len(ordered) - 1, int(round(p * (len(ordered) - 1))))], 1)

    return {"p50": q(0.50), "p90": q(0.90), "p99": q(0.99), "max": round(ordered[-1], 1)}


def _build_s3_client() -> Any:
    """Boto3 S3 client for the async store-check, from the S3_* env (vStorage)."""
    import boto3
    from botocore.client import Config

    endpoint = os.getenv("S3_ENDPOINT_URL")
    access = os.getenv("S3_ACCESS_KEY_ID")
    secret = os.getenv("S3_SECRET_ACCESS_KEY")
    if not (endpoint and access and secret):
        raise RuntimeError(
            "Store-check needs S3_ENDPOINT_URL, S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY "
            "(same vStorage creds the app uses). Set them, or pass --no-verify-store."
        )
    path_style = os.getenv("S3_FORCE_PATH_STYLE", "true").lower() in {"1", "true", "yes"}
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=os.getenv("S3_REGION", "us-east-1"),
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        # Wide connection pool so concurrent store-checks don't queue on botocore's default (10).
        config=Config(
            max_pool_connections=int(os.getenv("PERF_S3_POOL", "256")),
            s3={"addressing_style": "path" if path_style else "auto"},
        ),
    )


def _dummy_payload(index: int, data_source_id: str, run_id: str, rps: int) -> dict[str, Any]:
    return {
        "data_source_id": data_source_id,
        "session_id": f"perf-session-{index % 500}",
        "user_id": f"perf-user-{index}",
        "events": [
            {
                "event_name": "page_view",
                "page_url": f"https://perf.test/item/{index}",
                # --- performance-test data markers (for later identification / deletion) ---
                "_perf_test": True,
                "_perf_run_id": run_id,
                "_perf_rps": rps,
                "_perf_seq": index,
                "_perf_nonce": uuid4().hex,
            }
        ],
    }


async def _verify_stored(s3: Any, bucket: str, key: str, retries: int = 3) -> bool:
    """Async store-check: HEAD the object via boto3 in a worker thread (non-blocking)."""
    from botocore.exceptions import ClientError

    for attempt in range(retries):
        try:
            await asyncio.to_thread(s3.head_object, Bucket=bucket, Key=key)
            return True
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"} and attempt < retries - 1:
                await asyncio.sleep(0.2 * (attempt + 1))
                continue
            return False
        except Exception:  # noqa: BLE001 — a store-check failure must not kill the run
            if attempt < retries - 1:
                await asyncio.sleep(0.2 * (attempt + 1))
                continue
            return False
    return False


async def _send_one(
    index: int,
    client: httpx.AsyncClient,
    url: str,
    data_source_id: str,
    run_id: str,
    rps: int,
    s3: Optional[Any],
    sem: asyncio.Semaphore,
) -> ItemResult:
    res = ItemResult(index=index)
    payload = _dummy_payload(index, data_source_id, run_id, rps)
    async with sem:
        t0 = time.perf_counter()
        try:
            resp = await client.post(url, json=payload, headers={"User-Agent": USER_AGENT})
        except httpx.HTTPError as exc:
            res.send_ms = (time.perf_counter() - t0) * 1000
            res.error = f"send:{type(exc).__name__}"
            return res
        res.send_ms = (time.perf_counter() - t0) * 1000
        res.http_status = resp.status_code

        if resp.status_code == 429:
            # In ramp mode a 429 IS the failure signal — do not retry it away.
            res.throttled = True
            res.error = "http:429"
            return res
        if resp.status_code not in (200, 201):
            res.error = f"http:{resp.status_code}"
            return res
        try:
            data = resp.json()
        except ValueError:
            res.error = "bad-json"
            return res
        if data.get("filtered") or not data.get("accepted", True):
            res.error = f"rejected:{data.get('filter_reason', 'not-accepted')}"
            return res
        res.accepted = True

        if s3 is None:
            res.stored = True  # store-check disabled -> acceptance is success
            return res
        bucket, key = data.get("bucket"), data.get("object_key")
        if not bucket or not key:
            res.error = "no-object-key-in-response"
            return res
        v0 = time.perf_counter()
        res.stored = await _verify_stored(s3, bucket, key)
        res.verify_ms = (time.perf_counter() - v0) * 1000
        if not res.stored:
            res.error = "not-stored"
    return res


async def run_step(
    *,
    rps: int,
    per_step: int,
    client: httpx.AsyncClient,
    url: str,
    data_source_id: str,
    run_id: str,
    s3: Optional[Any],
) -> StepReport:
    """Fire ``per_step`` requests paced at ``rps`` (launch cadence independent of latency)."""
    rep = StepReport(rps=rps, count=per_step, verify_store=s3 is not None)
    interval = 1.0 / rps
    sem = asyncio.Semaphore(max(2 * rps, 200))  # bound in-flight without distorting healthy RPS
    send_lat: list[float] = []
    verify_lat: list[float] = []

    start = time.perf_counter()
    tasks: list[asyncio.Task] = []
    for i in range(per_step):
        due = start + i * interval
        gap = due - time.perf_counter()
        if gap > 0:
            await asyncio.sleep(gap)
        tasks.append(
            asyncio.create_task(
                _send_one(i, client, url, data_source_id, run_id, rps, s3, sem)
            )
        )
    for res in await asyncio.gather(*tasks):
        if res.send_ms:
            send_lat.append(res.send_ms)
        if rep.verify_store and res.verify_ms:
            verify_lat.append(res.verify_ms)
        if res.accepted:
            rep.accepted += 1
        if res.stored and res.accepted:
            rep.stored_ok += 1
        if res.throttled:
            rep.throttled += 1
        is_success = res.accepted and (res.stored or s3 is None) and res.error is None
        if is_success:
            rep.success += 1
        if res.error:
            rep.failed += 1
            rep.errors[res.error] = rep.errors.get(res.error, 0) + 1

    rep.wall_seconds = time.perf_counter() - start
    rep.achieved_rps = round(per_step / rep.wall_seconds, 1) if rep.wall_seconds else 0.0
    rep.success_rate = round(rep.success / per_step, 4) if per_step else 0.0
    rep.send_latency_ms = _pct(send_lat)
    rep.verify_latency_ms = _pct(verify_lat)
    return rep


@dataclass
class RampResult:
    run_id: str
    data_source_id: str
    bucket: str
    endpoint: str
    started_at: str
    total_seconds: float
    total_requests: int
    steps: list[dict[str, Any]]
    ceiling_rps: Optional[int]  # highest RPS that met the success threshold


async def run_ramp(
    *,
    start_rps: int,
    step_rps: int,
    max_rps: int,
    per_step: int,
    success_threshold: float,
    endpoint: str,
    data_source_id: str,
    verify_store: bool,
) -> RampResult:
    url = endpoint.rstrip("/") + TRACKING_PATH
    run_id = f"perf-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    bucket = f"data-tracking-{data_source_id}"
    s3 = _build_s3_client() if verify_store else None
    if s3 is not None:
        # asyncio.to_thread's default executor is tiny (~min(32, cpu+4)); widen it so the
        # async S3 store-checks run concurrently instead of serializing behind a few threads.
        pool = int(os.getenv("PERF_S3_POOL", "256"))
        asyncio.get_running_loop().set_default_executor(
            concurrent.futures.ThreadPoolExecutor(max_workers=pool, thread_name_prefix="s3-head")
        )

    logger.info("=" * 78)
    logger.info("RAMP START -> %s", url)
    logger.info("  run_id=%s  bucket=%s (deletion handle)", run_id, bucket)
    logger.info("  start_rps=%d step_rps=%d max_rps=%d per_step=%d threshold=%.2f%% verify_store=%s",
                start_rps, step_rps, max_rps, per_step, success_threshold * 100, verify_store)

    steps: list[dict[str, Any]] = []
    ceiling: Optional[int] = None
    total_requests = 0
    wall0 = time.perf_counter()
    limits = httpx.Limits(max_connections=max(2 * max_rps, 400), max_keepalive_connections=200)
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), limits=limits) as client:
        rps = start_rps
        while rps <= max_rps:
            logger.info("-" * 78)
            logger.info(">> STEP @ %d RPS  (%d requests)", rps, per_step)
            rep = await run_step(
                rps=rps, per_step=per_step, client=client, url=url,
                data_source_id=data_source_id, run_id=run_id, s3=s3,
            )
            total_requests += rep.count
            steps.append(_step_dict(rep))
            _log_step(rep)
            if rep.success_rate >= success_threshold:
                ceiling = rps
                rps += step_rps
            else:
                logger.info("   success rate %.2f%% < %.2f%% -> stopping ramp",
                            rep.success_rate * 100, success_threshold * 100)
                break
    total_seconds = time.perf_counter() - wall0
    if s3 is not None:
        try:
            s3.close()
        except Exception:  # noqa: BLE001
            pass

    result = RampResult(
        run_id=run_id, data_source_id=data_source_id, bucket=bucket, endpoint=url,
        started_at=datetime.now(timezone.utc).isoformat(), total_seconds=round(total_seconds, 3),
        total_requests=total_requests, steps=steps, ceiling_rps=ceiling,
    )
    _log_summary(result, success_threshold)
    return result


def _step_dict(rep: StepReport) -> dict[str, Any]:
    return {
        "rps": rep.rps,
        "requests": rep.count,
        "achieved_rps": rep.achieved_rps,
        "wall_seconds": round(rep.wall_seconds, 3),
        "accepted": rep.accepted,
        "stored_ok": rep.stored_ok if rep.verify_store else None,
        "success": rep.success,
        "success_rate": rep.success_rate,
        "throttled": rep.throttled,
        "failed": rep.failed,
        "send_latency_ms": rep.send_latency_ms,
        "verify_latency_ms": rep.verify_latency_ms if rep.verify_store else None,
        "errors": rep.errors,
    }


def _log_step(rep: StepReport) -> None:
    logger.info("   result: success %d/%d (%.2f%%)  achieved=%.1f rps  wall=%.1fs",
                rep.success, rep.count, rep.success_rate * 100, rep.achieved_rps, rep.wall_seconds)
    if rep.verify_store:
        logger.info("           accepted=%d stored=%d throttled=%d failed=%d",
                    rep.accepted, rep.stored_ok, rep.throttled, rep.failed)
    else:
        logger.info("           accepted=%d throttled=%d failed=%d", rep.accepted, rep.throttled, rep.failed)
    logger.info("           send_ms=%s store_ms=%s", rep.send_latency_ms, rep.verify_latency_ms or "-")
    if rep.errors:
        logger.info("           errors=%s", rep.errors)


def _log_summary(result: RampResult, threshold: float) -> None:
    logger.info("=" * 78)
    logger.info("RAMP DONE in %.2fs  total_requests=%d", result.total_seconds, result.total_requests)
    logger.info("  sustained ceiling (>= %.2f%% success): %s RPS",
                threshold * 100, result.ceiling_rps if result.ceiling_rps else "NONE (failed at first step)")
    logger.info("  per-step:")
    logger.info("    %-6s %-9s %-10s %-9s %-9s %s", "rps", "success", "rate", "achieved", "failed", "errors")
    for s in result.steps:
        logger.info("    %-6d %-9d %-10s %-9s %-9d %s",
                    s["rps"], s["success"], f"{s['success_rate'] * 100:.2f}%",
                    f"{s['achieved_rps']}", s["failed"], s["errors"] or "")
    logger.info("  PERF DATA -> bucket '%s'  (run_id=%s); events tagged _perf_test=true",
                result.bucket, result.run_id)
    logger.info("  to delete after the run: remove all objects in that bucket (or filter _perf_run_id).")
    logger.info("=" * 78)


def _config(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="UAT data-tracking stepped-RPS performance test")
    p.add_argument("--start-rps", type=int, default=int(os.getenv("PERF_START_RPS", "10")))
    p.add_argument("--step-rps", type=int, default=int(os.getenv("PERF_STEP_RPS", "10")))
    p.add_argument("--max-rps", type=int, default=int(os.getenv("PERF_MAX_RPS", "1000")))
    p.add_argument("--per-step", type=int, default=int(os.getenv("PERF_PER_STEP", "200")))
    p.add_argument("--success-threshold", type=float, default=float(os.getenv("PERF_SUCCESS_THRESHOLD", "0.99")))
    p.add_argument("--endpoint", default=os.getenv("TRACKING_ENDPOINT", DEFAULT_ENDPOINT))
    p.add_argument("--data-source-id", default=os.getenv("PERF_DATA_SOURCE_ID", DEFAULT_DATA_SOURCE_ID))
    p.add_argument("--out", default=os.getenv("PERF_OUT", ""))
    verify_default = os.getenv("PERF_VERIFY_STORE", "1").lower() in {"1", "true", "yes"}
    p.add_argument("--no-verify-store", dest="verify_store", action="store_false", default=verify_default)
    return p.parse_args(argv)


def run(argv: Optional[list[str]] = None) -> RampResult:
    args = _config(argv)
    result = asyncio.run(
        run_ramp(
            start_rps=args.start_rps, step_rps=args.step_rps, max_rps=args.max_rps,
            per_step=args.per_step, success_threshold=args.success_threshold,
            endpoint=args.endpoint, data_source_id=args.data_source_id, verify_store=args.verify_store,
        )
    )
    out = args.out or f"perf_results_{result.run_id}.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result.__dict__, fh, indent=2)
    logger.info("results written -> %s", out)
    return result


# --- pytest entry (skipped unless RUN_UAT_PERF=1 — never runs in ordinary CI) ---
def test_uat_performance_ramp() -> None:
    import pytest

    if os.getenv("RUN_UAT_PERF", "").lower() not in {"1", "true", "yes"}:
        pytest.skip("real-UAT perf ramp; set RUN_UAT_PERF=1 (and S3_* creds) to run")

    result = run([])
    assert result.steps, "ramp produced no steps"
    # The first step (start RPS) must be healthy — otherwise the endpoint/limit is misconfigured.
    assert result.steps[0]["success_rate"] >= float(os.getenv("PERF_SUCCESS_THRESHOLD", "0.99")), (
        f"first step failed: {result.steps[0]}"
    )


if __name__ == "__main__":
    run()
