"""Aggregate browser-reported API failures (4xx/5xx) and flush one digest per window."""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import FastAPI, Response
from pydantic import BaseModel, Field, field_validator

_log = logging.getLogger("customer360-frontend.errors")
MAX_KEYS = 500
_ID_SEGMENT = re.compile(
    r"^(\d+|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)


def normalize_path(url: str) -> str:
    """Drop scheme, host and query; replace UUID / numeric segments with ':id'."""
    try:
        path = urlsplit(url).path or "/"
    except ValueError:  # e.g. "http://[" -> would 500 the public endpoint
        return "/invalid"
    path = "".join(c for c in path if c.isprintable())  # block log-line injection
    return "/".join(":id" if _ID_SEGMENT.match(s) else s for s in path.split("/"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ErrorCollector:
    # ponytail: in-memory, lost on restart; move to Redis when multi-worker/multi-box
    def __init__(self, batch_size: int = 0, sink: Callable[[dict], None] | None = None) -> None:
        self.batch_size = batch_size  # 0 = flush on the timer only
        self.sink = sink or log_sink
        self._reset()

    def _reset(self) -> None:
        self._rows: dict[tuple[str, str, int], int] = {}
        self._dropped = 0
        self._start = _now()

    def record(self, method: str, url: str, status: int) -> None:
        key = (method.upper(), normalize_path(url), status)
        if key not in self._rows and len(self._rows) >= MAX_KEYS:
            self._dropped += 1
            return
        self._rows[key] = self._rows.get(key, 0) + 1

    @property
    def pending(self) -> int:
        return sum(self._rows.values()) + self._dropped

    def snapshot(self) -> dict:
        rows = sorted(
            (
                {"method": m, "path": p, "status": s, "count": n}
                for (m, p, s), n in self._rows.items()
            ),
            key=lambda r: -r["count"],
        )
        n4 = sum(r["count"] for r in rows if r["status"] < 500)
        n5 = sum(r["count"] for r in rows if r["status"] >= 500)
        return {
            "window_start": self._start,
            "window_end": _now(),
            "total": n4 + n5 + self._dropped,
            "count_4xx": n4,
            "count_5xx": n5,
            "dropped": self._dropped,
            "rows": rows,
        }

    def drain(self) -> dict:
        digest = self.snapshot()
        self._reset()
        return digest


def format_digest(d: dict) -> str:
    lines = [
        f"API error digest {d['window_start']} -> {d['window_end']}: "
        f"{d['total']} errors (4xx={d['count_4xx']}, 5xx={d['count_5xx']}, dropped={d['dropped']})"
    ]
    lines += [f"{r['count']:>5}x  {r['method']} {r['path']}  {r['status']}" for r in d["rows"]]
    return "\n".join(lines)


def log_sink(digest: dict) -> None:
    _log.warning("\n%s", format_digest(digest))


def flush(collector: ErrorCollector, sink: Callable[[dict], None] | None = None) -> None:
    """Hand the current window to the sink (default: the collector's); empty windows are skipped."""
    digest = collector.drain()
    if digest["rows"] or digest["dropped"]:
        (sink or collector.sink)(digest)


async def run_flush_loop(
    collector: ErrorCollector, interval: float, sink: Callable[[dict], None] | None = None
) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            flush(collector, sink)
        except Exception:  # a failing sink must not kill the loop
            _log.exception("error digest flush failed")


class ClientError(BaseModel):
    method: str = Field(pattern=r"^[A-Za-z]{1,10}$")
    url: str = Field(max_length=500)
    status: int = Field(ge=400, le=599)

    @field_validator("method")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


def register_error_routes(
    app: FastAPI, collector: ErrorCollector, root_path: str, is_dev: bool
) -> None:
    """Register POST /client-errors (+ dev-only GET summary) at standalone and prefixed paths."""

    async def report(payload: ClientError) -> Response:
        collector.record(payload.method, payload.url, payload.status)
        if collector.batch_size and collector.pending >= collector.batch_size:
            try:
                flush(collector)
            except Exception:  # reporting must never fail the browser's beacon
                _log.exception("batch flush failed")
        return Response(status_code=204)

    async def summary():
        return collector.snapshot()

    prefixes = ["/client-errors"]
    if root_path and root_path != "/":
        prefixes.append(f"{root_path}/client-errors")
    for prefix in prefixes:
        app.add_api_route(prefix, report, methods=["POST"], status_code=204, include_in_schema=False)
        if is_dev:
            app.add_api_route(f"{prefix}/summary", summary, methods=["GET"], include_in_schema=False)
