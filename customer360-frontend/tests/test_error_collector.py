"""Tests for the frontend API error collector."""

import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app_utils.error_collector import (
    MAX_KEYS,
    ErrorCollector,
    flush,
    format_digest,
    normalize_path,
    register_error_routes,
    run_flush_loop,
)
from app_utils.config import FrontendSettings
from app_utils.lifecycle import create_lifespan

UUID = "123e4567-e89b-12d3-a456-426614174000"


class NormalizePathTests(unittest.TestCase):
    def test_cases(self):
        for url, expected in (
            ("http://localhost:8008/api/v1/profiles/123?x=1", "/api/v1/profiles/:id"),
            (f"/api/v1/segments/{UUID}/members", "/api/v1/segments/:id/members"),
            ("https://h.com/api/v1/v2things", "/api/v1/v2things"),
            ("", "/"),
        ):
            with self.subTest(url=url):
                self.assertEqual(normalize_path(url), expected)


class CollectorTests(unittest.TestCase):
    def test_grouping_and_drain(self):
        c = ErrorCollector()
        c.record("get", "/api/p/1?a=b", 500)
        c.record("GET", "/api/p/2", 500)
        c.record("GET", "/api/q", 404)
        d = c.drain()
        self.assertEqual((d["total"], d["count_4xx"], d["count_5xx"]), (3, 1, 2))
        self.assertEqual(d["rows"][0]["path"], "/api/p/:id")
        self.assertEqual(d["rows"][0]["count"], 2)
        self.assertEqual(c.snapshot()["total"], 0)

    def test_key_cap(self):
        c = ErrorCollector()
        for i in range(MAX_KEYS + 5):
            c.record("GET", f"/p{i}", 500)
        d = c.snapshot()
        self.assertEqual(len(d["rows"]), MAX_KEYS)
        self.assertEqual(d["dropped"], 5)

    def test_format_digest(self):
        c = ErrorCollector()
        c.record("GET", "/api/p/1", 500)
        text = format_digest(c.drain())
        self.assertIn("1 errors (4xx=0, 5xx=1, dropped=0)", text)
        self.assertIn("1x  GET /api/p/:id  500", text)


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.collector = ErrorCollector()
        app = FastAPI()
        register_error_routes(app, self.collector, "/c360", True)
        self.client = TestClient(app)

    def test_valid_report(self):
        for path in ("/client-errors", "/c360/client-errors"):
            r = self.client.post(path, json={"method": "get", "url": "/api/x/9", "status": 404})
            self.assertEqual(r.status_code, 204)
        self.assertEqual(self.collector.snapshot()["rows"][0]["count"], 2)
        self.assertEqual(self.client.get("/client-errors/summary").json()["total"], 2)

    def test_invalid_reports(self):
        bad = {"method": "GET", "url": "/x", "status": 200}
        self.assertEqual(self.client.post("/client-errors", json=bad).status_code, 422)
        self.assertEqual(self.client.post("/client-errors", content=b"junk").status_code, 422)
        self.assertEqual(self.collector.snapshot()["total"], 0)


class EndpointBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.collector = ErrorCollector()
        app = FastAPI()
        register_error_routes(app, self.collector, "", False)
        self.client = TestClient(app)

    def post(self, **over):
        body = {"method": "GET", "url": "/x", "status": 500}
        body.update(over)
        return self.client.post("/client-errors", json=body).status_code

    def test_boundaries(self):
        for over, expected in (
            ({"status": 399}, 422), ({"status": 400}, 204),
            ({"status": 599}, 204), ({"status": 600}, 422),
            ({"method": "M" * 11}, 422), ({"method": "GET\nX"}, 422), ({"method": "M" * 10}, 204),
            ({"url": "/" * 500}, 204), ({"url": "/" * 501}, 422),
            ({"status": "500"}, 204),  # pydantic lax mode coerces numeric strings
            ({"status": 500.5}, 422),
            ({"extra": "ignored"}, 204),
        ):
            with self.subTest(over=over):
                self.assertEqual(self.post(**over), expected)

    def test_missing_fields_and_empty(self):
        for field in ("method", "url", "status"):
            body = {"method": "GET", "url": "/x", "status": 500}
            del body[field]
            with self.subTest(field=field):
                self.assertEqual(self.client.post("/client-errors", json=body).status_code, 422)
        self.assertEqual(self.client.post("/client-errors", json={}).status_code, 422)

    def test_summary_absent_when_not_dev(self):
        self.assertIn(self.client.get("/client-errors/summary").status_code, (404, 405))
        self.assertEqual(self.post(), 204)


class NormalizePathEdgeTests(unittest.TestCase):
    def test_cases(self):
        for url, expected in (
            ("/api/p/1/", "/api/p/:id/"),
            (f"/a/{UUID.upper()}", "/a/:id"),
            ("/", "/"),
            ("?a=1", "/"),
            ("/api/x#frag", "/api/x"),
            ("/a/507f1f77bcf86cd799439011", "/a/507f1f77bcf86cd799439011"),  # Mongo id not replaced
            ("/api/v1/x", "/api/v1/x"),
            ("/a/123abc", "/a/123abc"),
            ("/a//1", "/a//:id"),  # double slash preserved
            ("http://[", "/invalid"),
            ("/a\nFAKE LOG LINE\r", "/aFAKE LOG LINE"),
        ):
            with self.subTest(url=url):
                self.assertEqual(normalize_path(url), expected)


class CollectorExtraTests(unittest.TestCase):
    def test_method_case_merged_and_status_split(self):
        c = ErrorCollector()
        c.record("get", "/a", 500)
        c.record("GET", "/a", 500)
        c.record("GET", "/a", 502)
        rows = c.snapshot()["rows"]
        self.assertEqual([(r["status"], r["count"]) for r in rows], [(500, 2), (502, 1)])

    def test_sorted_by_count_desc(self):
        c = ErrorCollector()
        c.record("GET", "/a", 500)
        for _ in range(3):
            c.record("GET", "/b", 500)
        for _ in range(2):
            c.record("GET", "/c", 500)
        self.assertEqual([r["count"] for r in c.snapshot()["rows"]], [3, 2, 1])

    def test_existing_key_increments_after_cap(self):
        c = ErrorCollector()
        for i in range(MAX_KEYS):
            c.record("GET", f"/p{i}", 500)
        c.record("GET", "/p0", 500)
        c.record("GET", "/new", 500)
        d = c.snapshot()
        self.assertEqual(d["dropped"], 1)
        self.assertEqual(d["total"], MAX_KEYS + 1 + 1)
        self.assertEqual(d["rows"][0]["count"], 2)


class FlushTests(unittest.TestCase):
    def test_empty_skipped(self):
        sink = Mock()
        flush(ErrorCollector(), sink)
        sink.assert_not_called()

    def test_rows_delivered_and_drained(self):
        c, sink = ErrorCollector(), Mock()
        c.record("GET", "/a", 500)
        c.record("GET", "/b", 404)
        flush(c, sink)
        sink.assert_called_once()
        self.assertEqual(sink.call_args.args[0]["total"], 2)
        self.assertEqual(c.snapshot()["total"], 0)

    def test_dropped_only_delivered(self):
        c, sink = ErrorCollector(), Mock()
        c._dropped = 3
        flush(c, sink)
        sink.assert_called_once()
        self.assertEqual(sink.call_args.args[0]["dropped"], 3)

    def test_run_flush_loop(self):
        c, sink = ErrorCollector(), Mock()
        c.record("GET", "/a", 500)
        sleep = AsyncMock(side_effect=[None, None, asyncio.CancelledError()])
        with patch("app_utils.error_collector.asyncio.sleep", sleep):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(run_flush_loop(c, 5, sink))
        sink.assert_called_once()
        self.assertEqual(sleep.await_count, 3)


def _settings(**env):
    with patch.dict(os.environ, {"ERROR_COLLECTOR_FLUSH_SECONDS": "3600", **env}):
        return FrontendSettings.from_environment(Path("."))


class LifespanTests(unittest.TestCase):
    def _app(self, enabled):
        collector = ErrorCollector()
        app = FastAPI(lifespan=create_lifespan(_settings(ERROR_COLLECTOR_ENABLED=enabled), collector))
        register_error_routes(app, collector, "", False)
        return app, collector

    def test_shutdown_flushes_collector(self):
        app, collector = self._app("true")
        with patch("app_utils.lifecycle.flush", wraps=flush) as spy:
            with TestClient(app) as client:
                r = client.post("/client-errors", json={"method": "GET", "url": "/a/1", "status": 500})
                self.assertEqual(r.status_code, 204)
                spy.assert_not_called()
        spy.assert_called_once()
        self.assertIs(spy.call_args.args[0], collector)
        self.assertEqual(collector.snapshot()["total"], 0)  # drained on shutdown

    def test_shutdown_delivers_error_to_log(self):
        app, _ = self._app("true")
        with self.assertLogs("customer360-frontend.errors", "WARNING") as logs:
            with TestClient(app) as client:
                client.post("/client-errors", json={"method": "GET", "url": "/a/1", "status": 500})
        self.assertIn("GET /a/:id  500", logs.output[0])

    def test_disabled_starts_no_task(self):
        app, _ = self._app("false")
        with patch("app_utils.lifecycle.run_flush_loop") as loop, \
                patch("app_utils.lifecycle.flush") as fl:
            with TestClient(app):
                pass
        loop.assert_not_called()
        fl.assert_not_called()


class SettingsTests(unittest.TestCase):
    def test_enabled_values(self):
        for val, expected in (("false", False), ("0", False), ("no", False), ("FALSE", False),
                              ("true", True), ("yes", True), ("anything", True)):
            with self.subTest(val=val):
                self.assertIs(_settings(ERROR_COLLECTOR_ENABLED=val).error_collector_enabled, expected)

    def test_collector_path(self):
        for root, expected in (("", "/client-errors"), ("/c360", "/c360/client-errors"),
                               ("/", "/client-errors")):
            with self.subTest(root=root):
                self.assertEqual(_settings(FRONTEND_ROOT_PATH=root).error_collector_path, expected)


if __name__ == "__main__":
    unittest.main()
