"""Telegram sink for the API error digest: OpenAI one-paragraph summary + links to the errors."""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
import json
from urllib.parse import quote, urlencode

import httpx

from .config import FrontendSettings
from .error_collector import log_sink

_log = logging.getLogger("customer360-frontend.errors")
# httpx logs every request URL at INFO, and the Telegram URL contains the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
_pending: set[asyncio.Task] = set()  # strong refs so fire-and-forget tasks aren't GC'd
TOP_ROWS = 5
TELEGRAM_LIMIT = 4000  # API max is 4096


def jaeger_link(settings: FrontendSettings, tags: dict) -> str:
    """Jaeger UI search for failed requests of the API service in the last hour."""
    if not settings.jaeger_url:
        return ""
    q = urlencode({"service": settings.jaeger_service, "lookback": "1h", "tags": json.dumps(tags)})
    return f"{settings.jaeger_url}/search?{q}"


def _link(settings: FrontendSettings, row: dict) -> str:
    template = settings.error_collector_link_template
    if template:
        return template.format(
            method=quote(row["method"]), path=quote(row["path"], safe=""), status=row["status"]
        )
    # shortcut: path is not filterable (Jaeger records the route template, not our ':id' form);
    # method+status narrows enough for a drill-down.
    return jaeger_link(settings, {"http.method": row["method"], "http.status_code": str(row["status"])})


def build_message(digest: dict, summary: str | None, settings: FrontendSettings) -> str:
    lines = [
        f"🚨 C360 frontend: {digest['total']} API errors "
        f"(4xx={digest['count_4xx']}, 5xx={digest['count_5xx']})",
        f"{digest['window_start']} → {digest['window_end']}",
    ]
    if summary:
        lines += ["", summary.strip()]
    if all_errors := jaeger_link(settings, {"error": "true"}):
        lines += ["", f"All failing traces: {all_errors}"]
    lines.append("")
    for r in digest["rows"][:TOP_ROWS]:
        lines.append(f"{r['count']}x {r['method']} {r['path']} {r['status']}")
        if link := _link(settings, r):
            lines.append(f"  {link}")
    hidden = len(digest["rows"]) - TOP_ROWS
    if hidden > 0:
        lines.append(f"+{hidden} more endpoints")
    if digest["dropped"]:
        lines.append(f"({digest['dropped']} errors dropped: key cap reached)")
    return "\n".join(lines)[:TELEGRAM_LIMIT]


async def summarize(settings: FrontendSettings, digest: dict, client: httpx.AsyncClient) -> str | None:
    """2-3 line triage from OpenAI; only method/normalized path/status/count are sent."""
    if not settings.openai_api_key:
        return None
    rows = "\n".join(
        f"{r['count']}x {r['method']} {r['path']} {r['status']}" for r in digest["rows"][:20]
    )
    resp = await client.post(
        f"{settings.openai_base_url}/chat/completions",
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        json={
            "model": settings.openai_model,
            "temperature": 0.2,
            "max_tokens": 150,
            "messages": [
                {
                    "role": "system",
                    "content": "You triage web-app API failures for on-call engineers. In at most 3 "
                    "short plain-text lines: what is failing, the likely cause (4xx=client/auth/"
                    "validation, 5xx=server), and what to check first. No markdown, no preamble.",
                },
                {"role": "user", "content": rows},
            ],
        },
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


async def notify(settings: FrontendSettings, digest: dict) -> None:
    # Never log the exception text: httpx errors embed the URL, which holds the bot token.
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            summary = await summarize(settings, digest, client)
        except Exception as e:
            _log.warning("OpenAI summary failed (%s); sending digest without it", type(e).__name__)
            summary = None
        text = build_message(digest, summary, settings)
        # TELEGRAM_CHAT_ID may list several ids (the CI notifier allows , ; or whitespace).
        for chat_id in re.split(r"[,;\s]+", settings.telegram_chat_id.strip()):
            try:
                r = await client.post(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                    json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
                )
                r.raise_for_status()
            except Exception as e:
                _log.error("Telegram send failed (%s)", type(e).__name__)


def build_sink(settings: FrontendSettings) -> Callable[[dict], None]:
    """Log always; also send to Telegram when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set."""
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return log_sink

    def sink(digest: dict) -> None:
        log_sink(digest)
        try:
            task = asyncio.get_running_loop().create_task(notify(settings, digest))
        except RuntimeError:  # no event loop (sync caller): the log line is all we can do
            return
        _pending.add(task)
        task.add_done_callback(_pending.discard)

    return sink


async def wait_pending() -> None:
    if _pending:
        await asyncio.gather(*_pending, return_exceptions=True)
