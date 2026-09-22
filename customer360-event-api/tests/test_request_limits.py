"""Tests for tracking request body limits."""

import asyncio

from core.request_limits import RequestBodyLimitMiddleware


def test_request_body_limit_rejects_oversized_content_length():
    sent = []
    called = False

    async def app(_scope, _receive, _send):
        nonlocal called
        called = True

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/tracking/logs",
        "headers": [(b"content-length", b"11")],
    }

    asyncio.run(
        RequestBodyLimitMiddleware(app, max_body_bytes=10)(
            scope,
            lambda: None,
            send,
        )
    )

    assert not called
    assert sent[0]["status"] == 413


def test_request_body_limit_rejects_oversized_chunked_body():
    sent = []
    received = [
        {"type": "http.request", "body": b"123456", "more_body": True},
        {"type": "http.request", "body": b"78901", "more_body": False},
    ]

    async def app(_scope, receive, _send):
        while True:
            message = await receive()
            if not message.get("more_body"):
                return

    async def receive():
        return received.pop(0)

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/data/api/v1/track/email/webhook",
        "headers": [],
    }

    asyncio.run(
        RequestBodyLimitMiddleware(app, max_body_bytes=10)(scope, receive, send)
    )

    assert sent[0]["status"] == 413