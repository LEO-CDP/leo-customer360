"""Generate realistic anonymous web traffic for the UAT tracking API."""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5


LOGGER = logging.getLogger("uat_tracking_traffic_simulator")
DEFAULT_TRACKING_API_URL = "https://beta.leocdp.com/data/api/v1/tracking/logs"
DEFAULT_DATA_SOURCE_ID = "4512a4ab-9fe8-4a1a-9915-521fdaf9925a"


@dataclass(frozen=True)
class BrowserProfile:
	device_type: str
	user_agent: str


@dataclass(frozen=True)
class Page:
	url: str
	title: str


BROWSER_PROFILES = (
	BrowserProfile(
		"desktop",
		"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
		"(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
	),
	BrowserProfile(
		"mobile",
		"Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
		"AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
		"Mobile/15E148 Safari/604.1",
	),
	BrowserProfile(
		"tablet",
		"Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
		"(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
	),
)

PAGES = (
	Page("https://www.bigdatavietnam.org/", "Big Data Vietnam"),
	Page(
		"https://www.bigdatavietnam.org/2012/12/data-science-starter-kit.html",
		"Big Data Vietnam: Data Science Starter Kit",
	),
	Page(
		"https://www.bigdatavietnam.org/2012/12/about-mc2ads-project.html",
		"About the Big Data Vietnam Project",
	),
	Page(
		"https://www.bigdatavietnam.org/2013/01/big-data-analytics.html",
		"Big Data Analytics",
	),
)

REFERRERS = (
	"",
	"https://www.google.com/search?q=big+data+vietnam",
	"https://www.linkedin.com/",
	"https://news.ycombinator.com/",
)


class UatTrafficError(RuntimeError):
	"""Raised when a UAT tracking request cannot be accepted."""


@dataclass(frozen=True)
class UatTrafficConfig:
	tracking_api_url: str = DEFAULT_TRACKING_API_URL
	data_source_id: UUID = UUID(DEFAULT_DATA_SOURCE_ID)
	sessions: int = 25
	min_events: int = 3
	max_events: int = 7
	lookback_hours: int = 24
	concurrency: int = 2
	request_timeout_seconds: float = 15.0
	retries: int = 2
	seed: int | None = None

	def __post_init__(self) -> None:
		if not self.tracking_api_url.startswith(("http://", "https://")):
			raise ValueError("tracking_api_url must use http:// or https://")
		if self.sessions < 1:
			raise ValueError("sessions must be at least 1")
		if self.min_events < 1:
			raise ValueError("min_events must be at least 1")
		if self.max_events < self.min_events:
			raise ValueError("max_events must be greater than or equal to min_events")
		if self.lookback_hours < 1:
			raise ValueError("lookback_hours must be at least 1")
		if self.concurrency < 1:
			raise ValueError("concurrency must be at least 1")
		if self.request_timeout_seconds <= 0:
			raise ValueError("request_timeout_seconds must be greater than zero")
		if self.retries < 0:
			raise ValueError("retries cannot be negative")
		max_session_seconds = (self.max_events - 1) * 45 + 30
		if self.lookback_hours * 3600 < max_session_seconds:
			raise ValueError(
				"lookback_hours is too small for max_events; increase lookback_hours "
				"or reduce max_events"
			)


@dataclass(frozen=True)
class SimulatedSession:
	session_id: str
	anonymous_id: str
	user_agent: str
	payload: dict[str, Any]


class WebTrafficGenerator:
	"""Build ordered, anonymous browser journeys with unique session IDs."""

	def __init__(self, config: UatTrafficConfig, *, clock: datetime | None = None):
		self.config = config
		self.clock = (clock or datetime.now(timezone.utc)).astimezone(timezone.utc)
		self.rng = random.Random(config.seed)

	def generate(self) -> list[SimulatedSession]:
		return [self._generate_session(index) for index in range(self.config.sessions)]

	def _uuid(self, kind: str, session_index: int, event_index: int | None = None) -> UUID:
		if self.config.seed is None:
			return uuid4()
		parts = ["uat-web-traffic", str(self.config.seed), kind, str(session_index)]
		if event_index is not None:
			parts.append(str(event_index))
		return uuid5(NAMESPACE_URL, ":".join(parts))

	def _generate_session(self, session_index: int) -> SimulatedSession:
		profile = self.rng.choice(BROWSER_PROFILES)
		session_id = str(self._uuid("session", session_index))
		anonymous_id = self._uuid("anonymous", session_index).hex
		device_fingerprint = self._uuid("device", session_index).hex
		event_count = self.rng.randint(self.config.min_events, self.config.max_events)
		max_age = int(self.config.lookback_hours * 3600)
		minimum_age = (event_count - 1) * 45 + 30
		session_start = self.clock - timedelta(
			seconds=self.rng.randint(minimum_age, max_age)
		)

		events: list[dict[str, Any]] = []
		current_page = self.rng.choice(PAGES)
		referrer = self.rng.choice(REFERRERS)
		elapsed_seconds = 0
		for event_index in range(event_count):
			if event_index == 0:
				event_name = "page-view"
				event_data: dict[str, Any] = {}
			else:
				event_name, current_page, event_data = self._next_action(current_page)
			if event_index > 0:
				elapsed_seconds += self.rng.randint(2, 45)
			events.append(
				self._event(
					event_name=event_name,
					event_time=session_start + timedelta(seconds=elapsed_seconds),
					page=current_page,
					referrer=referrer,
					anonymous_id=anonymous_id,
					session_id=session_id,
					device_fingerprint=device_fingerprint,
					device_type=profile.device_type,
					event_data=event_data,
					session_index=session_index,
					event_index=event_index,
				)
			)
			referrer = current_page.url

		return SimulatedSession(
			session_id=session_id,
			anonymous_id=anonymous_id,
			user_agent=profile.user_agent,
			payload={
				"data_source_id": str(self.config.data_source_id),
				"session_id": session_id,
				"user_id": None,
				"events": events,
			},
		)

	def _next_action(self, current_page: Page) -> tuple[str, Page, dict[str, Any]]:
		roll = self.rng.random()
		if roll < 0.42:
			return "page-view", self.rng.choice(PAGES), {}
		if roll < 0.72:
			return "click", current_page, {
				"target": self.rng.choice(("article-link", "navigation", "read-more")),
				"label": self.rng.choice(("Read more", "Explore", "View article")),
			}
		if roll < 0.88:
			return "scroll", current_page, {
				"depth_percent": self.rng.choice((25, 50, 75, 100)),
			}
		query = self.rng.choice(("data science", "analytics", "machine learning"))
		return "search", current_page, {"query": query}

	def _event(
		self,
		*,
		event_name: str,
		event_time: datetime,
		page: Page,
		referrer: str,
		anonymous_id: str,
		session_id: str,
		device_fingerprint: str,
		device_type: str,
		event_data: dict[str, Any],
		session_index: int,
		event_index: int,
	) -> dict[str, Any]:
		return {
			"event_name": event_name,
			"event_time": _format_event_time(event_time),
			"page_url": quote(page.url, safe=""),
			"page_title": quote(page.title, safe=""),
			"referrer_url": quote(referrer, safe="") if referrer else "",
			"anonymous_id": anonymous_id,
			"session_id": session_id,
			"device_fingerprint": device_fingerprint,
			"event_data": event_data,
			"event_id": str(self._uuid("event", session_index, event_index)),
			"device_type": device_type,
		}


def _format_event_time(value: datetime) -> str:
	return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _post_session(
	config: UatTrafficConfig,
	session: SimulatedSession,
) -> dict[str, Any]:
	request_body = json.dumps(session.payload).encode("utf-8")
	request_headers = {
		"Accept": "application/json",
		"Content-Type": "application/json",
		"User-Agent": session.user_agent,
	}
	transient_statuses = {408, 425, 429, 500, 502, 503, 504}

	for attempt in range(config.retries + 1):
		request = urllib.request.Request(
			config.tracking_api_url,
			data=request_body,
			headers=request_headers,
			method="POST",
		)
		try:
			with urllib.request.urlopen(
				request,
				timeout=config.request_timeout_seconds,
			) as response:
				response_body = response.read().decode("utf-8")
			response_payload = json.loads(response_body)
			if not isinstance(response_payload, dict):
				raise UatTrafficError("tracking API returned a non-object response")
			if response_payload.get("accepted") is False:
				raise UatTrafficError(
					f"tracking API filtered session {session.session_id}: "
					f"{response_payload.get('filter_reason', 'unknown reason')}"
				)
			if response_payload.get("event_count") != len(session.payload["events"]):
				raise UatTrafficError(
					f"tracking API accepted an unexpected event count for session "
					f"{session.session_id}: {response_payload.get('event_count')}"
				)
			return response_payload
		except urllib.error.HTTPError as exc:
			body = exc.read().decode("utf-8", errors="replace")
			if exc.code not in transient_statuses or attempt >= config.retries:
				raise UatTrafficError(
					f"tracking API returned HTTP {exc.code} for session "
					f"{session.session_id}: {body}"
				) from exc
			LOGGER.warning(
				"Retrying session %s after HTTP %s (attempt %d/%d)",
				session.session_id,
				exc.code,
				attempt + 1,
				config.retries,
			)
			time.sleep(2**attempt)
		except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
			if attempt >= config.retries:
				raise UatTrafficError(
					f"tracking API request failed for session {session.session_id}: {exc}"
				) from exc
			time.sleep(2**attempt)

	raise UatTrafficError(f"tracking API request failed for session {session.session_id}")


def run(config: UatTrafficConfig, *, dry_run: bool = False) -> int:
	sessions = WebTrafficGenerator(config).generate()
	total_events = sum(len(session.payload["events"]) for session in sessions)
	LOGGER.info(
		"Prepared %d events across %d anonymous sessions for %s",
		total_events,
		len(sessions),
		config.tracking_api_url,
	)
	if dry_run:
		print(json.dumps(sessions[0].payload, indent=2))
		return 0

	failed = 0
	accepted_events = 0
	with ThreadPoolExecutor(max_workers=config.concurrency) as executor:
		futures = {
			executor.submit(_post_session, config, session): session
			for session in sessions
		}
		for completed in as_completed(futures):
			session = futures[completed]
			try:
				response = completed.result()
				accepted_events += int(response["event_count"])
				LOGGER.info(
					"Accepted %d events for session %s / anonymous ID %s",
					response["event_count"],
					session.session_id,
					session.anonymous_id,
				)
			except (UatTrafficError, ValueError, TypeError) as exc:
				failed += 1
				LOGGER.error("Session %s failed: %s", session.session_id, exc)

	print(
		f"UAT traffic complete: accepted_events={accepted_events} "
		f"failed_sessions={failed} total_sessions={len(sessions)}"
	)
	return 1 if failed else 0


def _env_int(name: str, default: int) -> int:
	return int(os.getenv(name, str(default)))


def _env_float(name: str, default: float) -> float:
	return float(os.getenv(name, str(default)))


def _parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument(
		"--url",
		default=os.getenv("UAT_TRACKING_API_URL", DEFAULT_TRACKING_API_URL),
	)
	parser.add_argument(
		"--data-source-id",
		type=UUID,
		default=UUID(os.getenv("UAT_TRACKING_DATA_SOURCE_ID", DEFAULT_DATA_SOURCE_ID)),
	)
	parser.add_argument("--sessions", type=int, default=_env_int("UAT_SESSIONS", 25))
	parser.add_argument("--min-events", type=int, default=_env_int("UAT_MIN_EVENTS", 3))
	parser.add_argument("--max-events", type=int, default=_env_int("UAT_MAX_EVENTS", 7))
	parser.add_argument(
		"--lookback-hours",
		type=int,
		default=_env_int("UAT_LOOKBACK_HOURS", 24),
	)
	parser.add_argument(
		"--concurrency",
		type=int,
		default=_env_int("UAT_CONCURRENCY", 2),
	)
	parser.add_argument(
		"--timeout",
		type=float,
		default=_env_float("UAT_REQUEST_TIMEOUT_SECONDS", 15),
	)
	parser.add_argument("--retries", type=int, default=_env_int("UAT_RETRIES", 2))
	parser.add_argument(
		"--seed",
		type=int,
		default=(int(os.environ["UAT_RANDOM_SEED"]) if "UAT_RANDOM_SEED" in os.environ else None),
	)
	parser.add_argument("--dry-run", action="store_true")
	parser.add_argument("--verbose", action="store_true")
	return parser.parse_args()


def main() -> int:
	args = _parse_args()
	config = UatTrafficConfig(
		tracking_api_url=args.url,
		data_source_id=args.data_source_id,
		sessions=args.sessions,
		min_events=args.min_events,
		max_events=args.max_events,
		lookback_hours=args.lookback_hours,
		concurrency=args.concurrency,
		request_timeout_seconds=args.timeout,
		retries=args.retries,
		seed=args.seed,
	)
	logging.basicConfig(
		level=logging.INFO if args.verbose else logging.WARNING,
		format="%(asctime)s %(levelname)s %(message)s",
	)
	return run(config, dry_run=args.dry_run)


if __name__ == "__main__":
	raise SystemExit(main())