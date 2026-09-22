"""ASGI request-size protection for tracking ingestion endpoints."""

import json
from typing import Any, Awaitable, Callable

from core.metrics import tracking_metrics


class RequestBodyTooLarge(Exception):
    """Raised when a tracking request exceeds its configured body limit."""


class RequestBodyLimitMiddleware:
    """Reject oversized tracking request bodies, including chunked requests."""

    def __init__(self, app: Callable[..., Awaitable[Any]], max_body_bytes: int):
        self.app = app
        self.max_body_bytes = max(1, int(max_body_bytes))

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if not _is_limited_request(scope):
            await self.app(scope, receive, send)
            return

        content_length = _header_value(scope.get("headers", []), b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_body_bytes:
                    tracking_metrics.increment("tracking_request_body_rejections_total")
                    await _send_body_limit_response(send, self.max_body_bytes)
                    return
            except ValueError:
                pass

        received_bytes = 0

        async def limited_receive() -> dict[str, Any]:
            nonlocal received_bytes
            message = await receive()
            if message.get("type") == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_body_bytes:
                    raise RequestBodyTooLarge
            return message

        response_started = False

        async def tracked_send(message: dict[str, Any]) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except RequestBodyTooLarge:
            if not response_started:
                tracking_metrics.increment("tracking_request_body_rejections_total")
                await _send_body_limit_response(send, self.max_body_bytes)


def _is_limited_request(scope: dict[str, Any]) -> bool:
    if scope.get("type") != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}:
        return False
    path = str(scope.get("path", ""))
    return "/tracking/" in path or "/track/email/" in path


def _header_value(headers: list[tuple[bytes, bytes]], name: bytes) -> bytes | None:
    for header_name, value in headers:
        if header_name.lower() == name:
            return value
    return None


async def _send_body_limit_response(send: Any, max_body_bytes: int) -> None:
    body = json.dumps(
        {
            "detail": f"Request body may not exceed {max_body_bytes} bytes",
        }
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})