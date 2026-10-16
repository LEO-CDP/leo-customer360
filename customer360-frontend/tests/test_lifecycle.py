"""Tests for frontend security response headers."""

import asyncio
import unittest

from fastapi import Request
from starlette.responses import Response

from app_utils.lifecycle import SecurityHeadersMiddleware


class SecurityHeadersMiddlewareTests(unittest.TestCase):
    def response_for(self, path: str, root_path: str = "") -> Response:
        request = Request(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "https",
                "path": path,
                "raw_path": path.encode(),
                "query_string": b"",
                "headers": [],
                "server": ("localhost", 443),
                "client": ("127.0.0.1", 12345),
                "root_path": root_path,
            }
        )

        async def call_next(_request: Request) -> Response:
            return Response()

        middleware = SecurityHeadersMiddleware(app=None)
        return asyncio.run(middleware.dispatch(request, call_next))

    def test_workflow_frame_allows_same_origin_embedding(self):
        for path, root_path in (
            ("/static/js/workflow/workflow-studio-frame.html", ""),
            ("/c360/static/js/workflow/workflow-studio-frame.html", "/c360"),
        ):
            with self.subTest(path=path, root_path=root_path):
                response = self.response_for(path, root_path)
                self.assertEqual(response.headers["X-Frame-Options"], "SAMEORIGIN")

    def test_other_frontend_responses_remain_unframeable(self):
        for path, root_path in (
            ("/", ""),
            ("/static/js/workflow/workflow-studio.js", ""),
            ("/c360/", "/c360"),
        ):
            with self.subTest(path=path, root_path=root_path):
                response = self.response_for(path, root_path)
                self.assertEqual(response.headers["X-Frame-Options"], "DENY")


if __name__ == "__main__":
    unittest.main()
