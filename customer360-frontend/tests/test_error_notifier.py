"""Tests for the Telegram error-digest sink and batch flushing."""

import asyncio
import json
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app_utils import error_notifier as en
from app_utils.config import FrontendSettings
from app_utils.error_collector import ErrorCollector, register_error_routes


def settings(**env):
    base = {"TELEGRAM_BOT_TOKEN": "SECRET", "TELEGRAM_CHAT_ID": "42", "OPENAI_API_KEY": "sk-x",
            "ERROR_COLLECTOR_LINK_TEMPLATE": "https://logs/?q={method}+{path}+{status}"}
    with patch.dict(os.environ, {**base, **env}):
        return FrontendSettings.from_environment(Path("."))


def digest(n=2):
    c = ErrorCollector()
    for i in range(n):
        c.record("GET", f"/api/v1/p{i}/1", 500)
    c.record("POST", "/api/v1/x", 404)
    return c.drain()


class MessageTests(unittest.TestCase):
    def test_message_has_summary_links_and_limits(self):
        msg = en.build_message(digest(8), "DB is down.", settings())
        self.assertIn("9 API errors (4xx=1, 5xx=8)", msg)
        self.assertIn("DB is down.", msg)
        self.assertIn("https://logs/?q=GET+%2Fapi%2Fv1%2Fp0%2F%3Aid+500", msg)
        self.assertIn("+4 more endpoints", msg)

    def test_no_template_no_links_and_truncated(self):
        s = settings(ERROR_COLLECTOR_LINK_TEMPLATE="")
        self.assertNotIn("https://", en.build_message(digest(), None, s))
        self.assertLessEqual(len(en.build_message(digest(), "x" * 9000, s)), en.TELEGRAM_LIMIT)

    def test_jaeger_links(self):
        s = settings(ERROR_COLLECTOR_LINK_TEMPLATE="", ERROR_COLLECTOR_JAEGER_URL="https://h/jaeger/")
        msg = en.build_message(digest(), None, s)
        self.assertIn("https://h/jaeger/search?service=customer360-api&lookback=1h&tags=%7B%22error%22%3A+%22true%22%7D", msg)
        self.assertIn("%22http.method%22%3A+%22GET%22%2C+%22http.status_code%22%3A+%22500%22", msg)


def fake_client_factory(calls, openai_ok=True, telegram_ok=True):
    def handler(req: httpx.Request):
        calls.append(req)
        if "openai" in req.url.host:
            return httpx.Response(200, json={"choices": [{"message": {"content": "Server error."}}]}) \
                if openai_ok else httpx.Response(500, text="boom")
        return httpx.Response(200 if telegram_ok else 401, json={"ok": telegram_ok})
    real = httpx.AsyncClient
    return lambda **kw: real(transport=httpx.MockTransport(handler), **kw)


class NotifyTests(unittest.TestCase):
    def run_notify(self, **kw):
        calls = []
        with patch.object(en.httpx, "AsyncClient", fake_client_factory(calls, **kw)):
            asyncio.run(en.notify(settings(), digest()))
        return calls

    def test_openai_then_telegram(self):
        calls = self.run_notify()
        self.assertEqual([c.url.host for c in calls], ["api.openai.com", "api.telegram.org"])
        sent = json.loads(calls[1].content)
        self.assertEqual(sent["chat_id"], "42")
        self.assertIn("Server error.", sent["text"])
        self.assertEqual(calls[0].headers["authorization"], "Bearer sk-x")

    def test_openai_failure_still_sends_digest(self):
        calls = self.run_notify(openai_ok=False)
        self.assertIn("API errors", json.loads(calls[-1].content)["text"])

    def test_telegram_failure_never_leaks_token_or_raises(self):
        with self.assertLogs("customer360-frontend.errors", "ERROR") as logs:
            self.run_notify(telegram_ok=False)
        self.assertNotIn("SECRET", "".join(logs.output))

    def test_multiple_chat_ids_each_get_the_message(self):
        calls = []
        with patch.object(en.httpx, "AsyncClient", fake_client_factory(calls)):
            asyncio.run(en.notify(settings(TELEGRAM_CHAT_ID="1, 2;3"), digest()))
        ids = [json.loads(c.content)["chat_id"] for c in calls if "telegram" in c.url.host]
        self.assertEqual(ids, ["1", "2", "3"])

    def test_no_openai_key_skips_summary(self):
        calls = []
        with patch.object(en.httpx, "AsyncClient", fake_client_factory(calls)):
            asyncio.run(en.notify(settings(OPENAI_API_KEY=""), digest()))
        self.assertEqual([c.url.host for c in calls], ["api.telegram.org"])


class SinkTests(unittest.TestCase):
    def test_unconfigured_is_log_only(self):
        self.assertIs(en.build_sink(settings(TELEGRAM_BOT_TOKEN="")), en.log_sink)

    def test_batch_flush_triggers_sink_at_threshold(self):
        sink = Mock()
        c = ErrorCollector(batch_size=3, sink=sink)
        app = FastAPI()
        register_error_routes(app, c, "", False)
        client = TestClient(app)
        for i in range(3):
            self.assertEqual(client.post("/client-errors", json={"method": "GET", "url": f"/a{i}", "status": 500}).status_code, 204)
            self.assertEqual(sink.call_count, 1 if i == 2 else 0)
        self.assertEqual(sink.call_args.args[0]["total"], 3)
        self.assertEqual(c.pending, 0)

    def test_failing_sink_does_not_fail_report(self):
        c = ErrorCollector(batch_size=1, sink=Mock(side_effect=RuntimeError("x")))
        app = FastAPI()
        register_error_routes(app, c, "", False)
        r = TestClient(app).post("/client-errors", json={"method": "GET", "url": "/a", "status": 500})
        self.assertEqual(r.status_code, 204)

    def test_sink_sends_to_telegram_from_event_loop(self):
        sent = Mock()

        async def fake_notify(s, d):
            sent(d["total"])

        async def go():
            with patch.object(en, "notify", fake_notify):
                en.build_sink(settings())(digest())
                await en.wait_pending()

        asyncio.run(go())
        sent.assert_called_once_with(3)


if __name__ == "__main__":
    unittest.main()
