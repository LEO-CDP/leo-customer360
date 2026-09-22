"""Server-side proxy routes for the docs assistant."""
from __future__ import annotations

import logging

import httpx
from fastapi import Body, FastAPI, HTTPException, Request

from .config import FrontendSettings

_log = logging.getLogger("customer360-frontend.docs")


class DocsProxy:
    """Forward browser docs requests to the private docs-search service."""

    def __init__(self, settings: FrontendSettings) -> None:
        self.settings = settings

    async def forward(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        request: Request | None = None,
    ):
        headers: dict[str, str] = {}
        if request:
            host = request.headers.get("host")
            if host:
                headers["Host"] = host
        if self.settings.docs_internal_secret:
            headers["X-Internal-Auth"] = self.settings.docs_internal_secret

        try:
            async with httpx.AsyncClient(
                timeout=self.settings.docs_search_timeout
            ) as client:
                response = await client.request(
                    method,
                    f"{self.settings.docs_search_url}{path}",
                    json=payload,
                    headers=headers or None,
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            _log.warning(
                "Docs service %s %s -> HTTP %s",
                method,
                path,
                exc.response.status_code,
            )
            raise HTTPException(
                status_code=502,
                detail="The documentation service returned an error.",
            ) from exc
        except httpx.HTTPError as exc:
            _log.warning("Docs service %s %s unreachable: %s", method, path, exc)
            raise HTTPException(
                status_code=502,
                detail="The documentation service is unreachable.",
            ) from exc

    async def ask(self, request: Request, payload: dict = Body(...)):
        """Forward a grounded answer request."""
        question = str(payload.get("question", "")).strip()
        if not question:
            raise HTTPException(status_code=422, detail="question is required")
        body: dict[str, object] = {
            "question": question[: self.settings.docs_max_question_length]
        }
        if isinstance(payload.get("top_n"), int):
            body["top_n"] = payload["top_n"]
        if isinstance(payload.get("top_k"), int):
            body["top_k"] = payload["top_k"]
        return await self.forward("POST", "/ask", body, request)

    async def search(self, request: Request, payload: dict = Body(...)):
        """Forward a semantic search request."""
        query = str(payload.get("query", "")).strip()
        if not query:
            raise HTTPException(status_code=422, detail="query is required")
        body: dict[str, object] = {
            "query": query[: self.settings.docs_max_question_length]
        }
        if isinstance(payload.get("top_n"), int):
            body["top_n"] = payload["top_n"]
        return await self.forward("POST", "/search", body, request)

    async def health(self, request: Request):
        """Forward the docs service health request."""
        return await self.forward("GET", "/health", request=request)


def register_docs_routes(app: FastAPI, proxy: DocsProxy, root_path: str) -> None:
    """Register docs proxy routes at both standalone and prefixed paths."""
    prefixes = ["/ai"]
    if root_path and root_path != "/":
        prefixes.append(f"{root_path}/ai")

    routes = (
        ("/ask", proxy.ask, ["POST"]),
        ("/search", proxy.search, ["POST"]),
        ("/health", proxy.health, ["GET"]),
    )
    for prefix in prefixes:
        for suffix, endpoint, methods in routes:
            app.add_api_route(
                f"{prefix}{suffix}",
                endpoint,
                methods=methods,
                include_in_schema=False,
            )
