"""Generate fresh synthetic traffic and submit it only to customer360-event-api.

This command deliberately has no database, S3, MinIO, Docker, or analytics
integration. The tracking API owns validation, durable storage, and any later
processing of the accepted events.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from dotenv import load_dotenv

from web_user_simulator import TrackingApiError, TrackingLogClient


LOGGER = logging.getLogger("seed_api_data")
DEFAULT_TRACKING_API_URL = "http://localhost:8010/api/v1/tracking/logs"
DEFAULT_DATA_SOURCE_ID = "15dc39d4-ae42-5c60-9c77-66f05dcae448"
DEFAULT_EVENT_COUNT = 20_000
DEFAULT_LOOKBACK_HOURS = 48
DEFAULT_EVENTS_PER_SESSION = 20
DEFAULT_CONCURRENCY = 4
DEFAULT_PROFILE_COUNT = 1_000

EVENT_TEMPLATES = (
    ("page_view", "GENERAL", "web", "product"),
    ("product_view", "GENERAL", "web", "product"),
    ("add_to_cart", "COMMERCE", "web", "product"),
    ("search", "GENERAL", "web", "catalog"),
    ("wishlist_add", "COMMERCE", "mobile_app", "product"),
    ("purchase", "COMMERCE", "web", "product"),
    ("support_request", "SERVICE_INDUSTRY", "web", "support"),
    ("app_open", "GENERAL", "mobile_app", "app"),
)
DOMAINS = ("retail", "education", "real_estate", "travel")
DEVICE_PROFILES = (
    (
        "desktop",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
    ),
    (
        "mobile",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    ),
    (
        "tablet",
        "Mozilla/5.0 (iPad; CPU OS 13_2 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/13.0 Mobile/15E148 Safari/604.1",
    ),
)


@dataclass(frozen=True)
class ApiSeedConfig:
    """Validated configuration for API-only traffic generation."""

    tracking_api_url: str = DEFAULT_TRACKING_API_URL
    data_source_id: UUID = UUID(DEFAULT_DATA_SOURCE_ID)
    event_count: int = DEFAULT_EVENT_COUNT
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS
    events_per_session: int = DEFAULT_EVENTS_PER_SESSION
    concurrency: int = DEFAULT_CONCURRENCY
    profile_count: int = DEFAULT_PROFILE_COUNT
    request_timeout_seconds: float = 10.0
    queue_drain_timeout_seconds: float = 180.0
    queue_poll_interval_seconds: float = 2.0
    seed: int | None = None

    def __post_init__(self) -> None:
        if not self.tracking_api_url.startswith(("http://", "https://")):
            raise ValueError("tracking_api_url must use http:// or https://")
        if self.event_count < 1:
            raise ValueError("event_count must be at least 1")
        if self.lookback_hours < 1:
            raise ValueError("lookback_hours must be at least 1")
        if self.events_per_session < 1:
            raise ValueError("events_per_session must be at least 1")
        if self.concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        if self.profile_count < 1:
            raise ValueError("profile_count must be at least 1")
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be greater than zero")
        if self.queue_drain_timeout_seconds < 0:
            raise ValueError("queue_drain_timeout_seconds cannot be negative")
        if self.queue_poll_interval_seconds <= 0:
            raise ValueError("queue_poll_interval_seconds must be greater than zero")

    @classmethod
    def from_environment(cls) -> "ApiSeedConfig":
        simulator_dir = os.path.dirname(__file__)
        load_dotenv(os.path.join(simulator_dir, ".env"))
        load_dotenv(os.path.join(simulator_dir, "..", ".env"))
        load_dotenv(".env")
        return cls(
            tracking_api_url=os.getenv("SEED_TRACKING_API_URL", DEFAULT_TRACKING_API_URL),
            data_source_id=UUID(
                os.getenv("SEED_TRACKING_DATA_SOURCE_ID", DEFAULT_DATA_SOURCE_ID)
            ),
            event_count=int(os.getenv("NEW_DATA_EVENT_COUNT", str(DEFAULT_EVENT_COUNT))),
            lookback_hours=int(
                os.getenv("NEW_DATA_LOOKBACK_HOURS", str(DEFAULT_LOOKBACK_HOURS))
            ),
            events_per_session=int(
                os.getenv(
                    "SEED_EVENTS_PER_SESSION",
                    str(DEFAULT_EVENTS_PER_SESSION),
                )
            ),
            concurrency=int(os.getenv("SEED_API_CONCURRENCY", str(DEFAULT_CONCURRENCY))),
            profile_count=int(
                os.getenv("SEED_PROFILE_COUNT", str(DEFAULT_PROFILE_COUNT))
            ),
            request_timeout_seconds=float(
                os.getenv("TRACKING_REQUEST_TIMEOUT_SECONDS", "10")
            ),
            queue_drain_timeout_seconds=float(
                os.getenv("SEED_QUEUE_DRAIN_TIMEOUT_SECONDS", "180")
            ),
            queue_poll_interval_seconds=float(
                os.getenv("SEED_QUEUE_POLL_INTERVAL_SECONDS", "2")
            ),
            seed=(int(os.environ["SEED_API_RANDOM_SEED"]) if "SEED_API_RANDOM_SEED" in os.environ else None),
        )


class ApiTrafficGenerator:
    """Create realistic user sessions without knowing database state."""

    def __init__(self, config: ApiSeedConfig, *, clock: datetime | None = None) -> None:
        self.config = config
        self.clock = (clock or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self.rng = random.Random(config.seed)

    def iter_sessions(self) -> Iterator[dict[str, Any]]:
        """Yield API request payloads, keeping each request user/session scoped."""
        remaining = self.config.event_count
        session_index = 0
        lookback = timedelta(hours=self.config.lookback_hours)
        while remaining:
            session_index += 1
            event_count = min(remaining, self.config.events_per_session)
            profile_index = session_index % self.config.profile_count
            user_id = f"api-seed-user-{profile_index:06d}"
            session_id = self._identifier("session", session_index)
            device_type, user_agent = self.rng.choice(DEVICE_PROFILES)
            events = [
                self._event(
                    session_index=session_index,
                    event_index=index,
                    user_id=user_id,
                    session_id=session_id,
                    event_time=self.clock - timedelta(
                        seconds=self.rng.randint(0, int(lookback.total_seconds()))
                    ),
                    device_type=device_type,
                )
                for index in range(event_count)
            ]
            yield {
                "session_id": session_id,
                "user_id": user_id,
                "device_type": device_type,
                "user_agent": user_agent,
                "events": events,
            }
            remaining -= event_count

    def _identifier(self, prefix: str, index: int) -> str:
        if self.config.seed is None:
            return f"{prefix}-{uuid4().hex}"
        return str(uuid5(NAMESPACE_URL, f"api-seed:{self.config.seed}:{prefix}:{index}"))

    def _event(
        self,
        *,
        session_index: int,
        event_index: int,
        user_id: str,
        session_id: str,
        event_time: datetime,
        device_type: str,
    ) -> dict[str, Any]:
        event_name, event_category, platform, entity_type = self.rng.choice(EVENT_TEMPLATES)
        event_id = self._identifier(
            "event",
            session_index * self.config.events_per_session + event_index,
        )
        domain = self.rng.choice(DOMAINS)
        is_conversion = event_name == "purchase"
        return {
            "event_id": event_id,
            "event_time": event_time.isoformat(),
            "event_dedup_key": f"api-seed:{event_id}",
            "event_name": event_name,
            "event_category": event_category,
            "source_system": "api-seed-simulator",
            "domain": domain,
            "user_id": user_id,
            "session_id": session_id,
            "device_id": f"api-seed-device-{user_id}",
            "external_customer_id": user_id,
            "device_type": device_type,
            "platform": platform,
            "entity_type": entity_type,
            "entity_id": f"{entity_type}-{self.rng.randint(1, 50_000):05d}",
            "is_conversion": is_conversion,
            "event_value": (
                round(self.rng.uniform(150_000, 3_000_000), 2)
                if is_conversion
                else None
            ),
            "currency": "VND",
            "transaction_id": event_id if is_conversion else None,
            "transaction_status": "completed" if is_conversion else None,
            "properties": {
                "traffic_type": "synthetic_internet",
                "simulator": "all-data-simulator/seed_api_data.py",
                "session_index": session_index,
                "event_index": event_index,
            },
        }


def _send_session(
    tracker: TrackingLogClient,
    config: ApiSeedConfig,
    session: dict[str, Any],
) -> dict[str, Any]:
    response = tracker.send(
        data_source_id=config.data_source_id,
        session_id=session["session_id"],
        user_id=session["user_id"],
        events=session["events"],
        user_agent=session["user_agent"],
    )
    return {"event_count": len(session["events"]), "response": response}


def _queue_status_url(tracking_api_url: str) -> str:
    parsed = urllib.parse.urlsplit(tracking_api_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/logs"):
        path = f"{path[:-len('/logs')]}/queue-status"
    else:
        path = f"{path}/queue-status"
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment)
    )


def wait_for_tracking_queue(config: ApiSeedConfig) -> bool:
    """Wait for the API's asynchronous object-storage queue to drain."""
    if config.queue_drain_timeout_seconds == 0:
        return True

    status_url = _queue_status_url(config.tracking_api_url)
    deadline = time.monotonic() + config.queue_drain_timeout_seconds
    while True:
        request = urllib.request.Request(
            status_url,
            headers={"Accept": "application/json", "User-Agent": "leo-api-seed/1.0"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=config.request_timeout_seconds,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
            queue_depth = int(payload.get("queue_depth", 0))
        except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
            LOGGER.warning("Could not read tracking API queue status at %s: %s", status_url, exc)
            return False

        if queue_depth <= 0:
            LOGGER.info("Tracking API queue drained; accepted objects are ready for downstream processing")
            return True
        if time.monotonic() >= deadline:
            LOGGER.warning(
                "Tracking API queue did not drain within %.1f seconds (queue_depth=%d)",
                config.queue_drain_timeout_seconds,
                queue_depth,
            )
            return False
        LOGGER.info("Waiting for tracking API queue to drain (queue_depth=%d)", queue_depth)
        time.sleep(config.queue_poll_interval_seconds)


def run_api_seed(config: ApiSeedConfig, *, dry_run: bool = False) -> int:
    """Generate sessions and POST them to the tracking API only."""
    sessions = list(ApiTrafficGenerator(config).iter_sessions())
    total_events = sum(len(session["events"]) for session in sessions)
    LOGGER.info(
        "Prepared %d simulated events in %d API request(s) for endpoint %s and data source %s",
        total_events,
        len(sessions),
        config.tracking_api_url,
        config.data_source_id,
    )
    if dry_run:
        return 0

    tracker = TrackingLogClient(
        config.tracking_api_url,
        config.request_timeout_seconds,
    )
    failed = 0
    accepted_events = 0
    with ThreadPoolExecutor(max_workers=config.concurrency) as executor:
        futures = [
            executor.submit(_send_session, tracker, config, session)
            for session in sessions
        ]
        for session_index, future in enumerate(as_completed(futures), start=1):
            try:
                result = future.result()
                accepted_events += result["event_count"]
                LOGGER.info(
                    "Accepted API request %d/%d (%d events)",
                    session_index,
                    len(futures),
                    result["event_count"],
                )
            except TrackingApiError as exc:
                failed += 1
                LOGGER.error("Tracking API request failed: %s", exc)

    LOGGER.info(
        "API seed complete: accepted_events=%d failed_requests=%d",
        accepted_events,
        failed,
    )
    if failed == 0:
        wait_for_tracking_queue(config)
    return 1 if failed else 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=None)
    parser.add_argument("--lookback-hours", type=int, default=None)
    parser.add_argument("--events-per-session", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--profile-count", type=int, default=None)
    parser.add_argument("--tracking-url", default=None)
    parser.add_argument("--data-source-id", default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--queue-timeout", type=float, default=None)
    parser.add_argument("--no-queue-wait", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    config = ApiSeedConfig.from_environment()
    overrides: dict[str, Any] = {}
    if args.events is not None:
        overrides["event_count"] = args.events
    if args.lookback_hours is not None:
        overrides["lookback_hours"] = args.lookback_hours
    if args.events_per_session is not None:
        overrides["events_per_session"] = args.events_per_session
    if args.concurrency is not None:
        overrides["concurrency"] = args.concurrency
    if args.profile_count is not None:
        overrides["profile_count"] = args.profile_count
    if args.tracking_url is not None:
        overrides["tracking_api_url"] = args.tracking_url
    if args.data_source_id is not None:
        overrides["data_source_id"] = UUID(args.data_source_id)
    if args.seed is not None:
        overrides["seed"] = args.seed
    if args.queue_timeout is not None:
        overrides["queue_drain_timeout_seconds"] = args.queue_timeout
    if args.no_queue_wait:
        overrides["queue_drain_timeout_seconds"] = 0
    if overrides:
        from dataclasses import replace

        config = replace(config, **overrides)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return run_api_seed(config, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())