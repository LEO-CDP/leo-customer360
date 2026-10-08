from __future__ import annotations

from fastapi import HTTPException
from starlette.requests import Request

from src import server


def _request(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/reindex",
            "headers": headers,
            "client": ("127.0.0.1", 1234),
            "server": ("docs", 8001),
        }
    )


def test_reindex_access_requires_the_internal_secret(monkeypatch):
    monkeypatch.setattr(server, "INTERNAL_API_SECRET", "test-secret")
    monkeypatch.setattr(server.limiter, "is_internal", lambda request: False)

    try:
        server._require_reindex_access(_request([]))
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("unauthenticated reindex access was accepted")


def test_reindex_access_accepts_a_trusted_internal_request(monkeypatch):
    monkeypatch.setattr(server, "INTERNAL_API_SECRET", "test-secret")
    monkeypatch.setattr(server.limiter, "is_internal", lambda request: True)

    server._require_reindex_access(_request([(b"x-internal-auth", b"test-secret")]))

def test_profile_context_is_accepted_only_from_a_trusted_caller(monkeypatch):
    monkeypatch.setattr(server, "INTERNAL_API_SECRET", "test-secret")
    ask = server.AskRequest(question="Why?", context=["Churn risk tier: high"])

    monkeypatch.setattr(server.limiter, "is_internal", lambda request: False)
    try:
        server._trusted_context(ask, _request([]))
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("browser-supplied context was accepted")

    # Without context nothing needs trust, and a trusted caller's context passes through.
    assert server._trusted_context(server.AskRequest(question="Why?"), _request([])) == (None, None, None, None)
    monkeypatch.setattr(server.limiter, "is_internal", lambda request: True)
    assert server._trusted_context(ask, _request([(b"x-internal-auth", b"test-secret")])) == (
        ["Churn risk tier: high"],
        None,
        None,
        None,
    )


def test_view_and_dialog_are_accepted_only_from_a_trusted_caller(monkeypatch):
    monkeypatch.setattr(server, "INTERNAL_API_SECRET", "test-secret")
    ask = server.AskRequest(question="What does this tab show?", view="Agent Workflow", dialog="Add Data Source")

    monkeypatch.setattr(server.limiter, "is_internal", lambda request: False)
    for req in (ask, server.AskRequest(question="Q?", view="Agent Workflow"), server.AskRequest(question="Q?", dialog="Add Data Source")):
        try:
            server._trusted_context(req, _request([]))
        except HTTPException as exc:
            assert exc.status_code == 403
            assert "context, context_title, view and dialog require" in exc.detail
        else:
            raise AssertionError("browser-supplied view/dialog was accepted")

    monkeypatch.setattr(server.limiter, "is_internal", lambda request: True)
    assert server._trusted_context(ask, _request([(b"x-internal-auth", b"test-secret")])) == (
        None,
        "Agent Workflow",
        "Add Data Source",
        None,
    )


def test_context_title_is_accepted_only_from_a_trusted_caller(monkeypatch):
    monkeypatch.setattr(server, "INTERNAL_API_SECRET", "test-secret")
    ask = server.AskRequest(question="Why is this segment empty?", context=["Members: 0"], context_title="Segment on screen")

    monkeypatch.setattr(server.limiter, "is_internal", lambda request: False)
    for req in (ask, server.AskRequest(question="Q?", context_title="Segment on screen")):
        try:
            server._trusted_context(req, _request([]))
        except HTTPException as exc:
            assert exc.status_code == 403
            assert "context, context_title, view and dialog require" in exc.detail
        else:
            raise AssertionError("browser-supplied context_title was accepted")

    monkeypatch.setattr(server.limiter, "is_internal", lambda request: True)
    assert server._trusted_context(ask, _request([(b"x-internal-auth", b"test-secret")])) == (
        ["Members: 0"],
        None,
        None,
        "Segment on screen",
    )


def test_ask_passes_page_context_fields_to_the_query(monkeypatch):
    monkeypatch.setattr(server, "INTERNAL_API_SECRET", "test-secret")
    monkeypatch.setattr(server.limiter, "is_internal", lambda request: True)
    monkeypatch.setattr(server.limiter, "enforce", lambda request: _noop())
    seen = {}

    def fake_query(question, top_n, top_k, page, context, view, dialog, context_title):
        seen.update(
            question=question,
            page=page,
            context=context,
            view=view,
            dialog=dialog,
            context_title=context_title,
        )
        return {"sources": []}

    monkeypatch.setattr(server, "_query_sync", fake_query)

    import asyncio

    req = server.AskRequest(
        question="What does this tab show?",
        page="/segments/:id",
        context=["Members: 0"],
        context_title="Segment on screen",
        view="Agent Workflow",
        dialog="Add Data Source",
    )
    asyncio.run(server.ask(req, _request([(b"x-internal-auth", b"test-secret")])))

    assert seen == {
        "question": "What does this tab show?",
        "page": "/segments/:id",
        "context": ["Members: 0"],
        "view": "Agent Workflow",
        "dialog": "Add Data Source",
        "context_title": "Segment on screen",
    }


async def _noop():
    return None


def test_ask_request_limits_page_context_title_view_and_dialog_size():
    for bad in (
        {"page": "x" * 121},
        {"context": ["x"] * 41},
        {"context_title": "x" * 61},
        {"view": "x" * 61},
        {"dialog": "x" * 81},
    ):
        try:
            server.AskRequest(question="Q?", **bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad.keys()}")
