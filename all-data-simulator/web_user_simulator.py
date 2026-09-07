"""AI-driven ecommerce user simulator for the Customer 360 tracking API.

The simulator uses an OpenAI-compatible chat model to choose actions from a
small set of ecommerce tools. The tools execute locally, record tracking
events, and submit one event batch per simulated user to the tracking API.
When no API key is configured, the same journey runs in deterministic offline
mode so the data source can still be exercised in local environments.
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from dotenv import load_dotenv


LOGGER = logging.getLogger("web_user_simulator")
DEFAULT_TRACKING_API_URL = "https://c360.example.com/data/api/v1/tracking/logs"
DEFAULT_DATA_SOURCE_ID = "11111111-1111-1111-1111-111111111111"
DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"

PRODUCT_CATALOG = (
	{
		"product_id": "product-001",
		"name": "Nimbus noise-cancelling earbuds",
		"category": "audio",
		"price_vnd": 1_490_000,
		"description": "Wireless earbuds with active noise cancellation and a 30-hour case.",
	},
	{
		"product_id": "product-002",
		"name": "Trailway commuter backpack",
		"category": "travel",
		"price_vnd": 1_290_000,
		"description": "A water-resistant backpack with a laptop sleeve and hidden passport pocket.",
	},
	{
		"product_id": "product-003",
		"name": "Sol desk lamp",
		"category": "home",
		"price_vnd": 790_000,
		"description": "An adjustable warm-to-cool LED desk lamp with USB-C charging.",
	},
	{
		"product_id": "product-004",
		"name": "Pace running shoes",
		"category": "fitness",
		"price_vnd": 1_890_000,
		"description": "Lightweight daily trainers with responsive foam for road running.",
	},
)

AD_CATALOG = (
	{
		"ad_id": "ad-001",
		"campaign": "summer_audio_sale",
		"product_id": "product-001",
		"placement": "homepage_banner",
	},
	{
		"ad_id": "ad-002",
		"campaign": "commute_ready",
		"product_id": "product-002",
		"placement": "search_results",
	},
	{
		"ad_id": "ad-003",
		"campaign": "work_from_home_refresh",
		"product_id": "product-003",
		"placement": "recommendation_feed",
	},
	{
		"ad_id": "ad-004",
		"campaign": "move_more",
		"product_id": "product-004",
		"placement": "homepage_banner",
	},
)

TOOL_DEFINITIONS = [
	{
		"type": "function",
		"function": {
			"name": "see_ad",
			"description": "View one advertised product. Start the journey with this action.",
			"parameters": {
				"type": "object",
				"properties": {"ad_id": {"type": "string"}},
				"required": ["ad_id"],
				"additionalProperties": False,
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "view_product",
			"description": "Open the product detail page for a product seen in an ad.",
			"parameters": {
				"type": "object",
				"properties": {"product_id": {"type": "string"}},
				"required": ["product_id"],
				"additionalProperties": False,
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "ask_price",
			"description": "Ask the store for the current price of a product.",
			"parameters": {
				"type": "object",
				"properties": {"product_id": {"type": "string"}},
				"required": ["product_id"],
				"additionalProperties": False,
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "ask_support",
			"description": "Ask customer support a question about the product or order.",
			"parameters": {
				"type": "object",
				"properties": {
					"product_id": {"type": "string"},
					"question": {"type": "string"},
				},
				"required": ["product_id", "question"],
				"additionalProperties": False,
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "purchase_product",
			"description": "Purchase the product after viewing it and checking its price or support details.",
			"parameters": {
				"type": "object",
				"properties": {
					"product_id": {"type": "string"},
					"quantity": {"type": "integer", "minimum": 1, "maximum": 2},
				},
				"required": ["product_id", "quantity"],
				"additionalProperties": False,
			},
		},
	},
]


class TrackingApiError(RuntimeError):
	"""Raised when the tracking endpoint rejects or cannot receive a batch."""


class TrackingVerificationError(RuntimeError):
	"""Raised when a tracking batch cannot be found or does not match."""


@dataclass(frozen=True)
class AgentConfig:
	"""Runtime configuration for one simulator process."""

	tracking_api_url: str = DEFAULT_TRACKING_API_URL
	data_source_id: UUID = UUID(DEFAULT_DATA_SOURCE_ID)
	openai_api_key: str | None = None
	openai_model: str = DEFAULT_OPENAI_MODEL
	openai_base_url: str | None = None
	openai_reasoning_effort: str = "none"
	request_timeout_seconds: float = 10.0
	max_steps: int = 8
	offline: bool = False
	minio_endpoint: str = "localhost:9000"
	minio_access_key: str | None = None
	minio_secret_key: str | None = None
	minio_secure: bool = False
	s3_verify_wait_seconds: float = 5.0
	s3_verify_retry_seconds: float = 2.0
	s3_verify_attempts: int = 3
	verify_s3: bool = True

	@classmethod
	def from_environment(cls) -> "AgentConfig":
		"""Build configuration from the simulator's environment variables."""
		simulator_dir = os.path.dirname(__file__)
		load_dotenv(os.path.join(simulator_dir, ".env"))
		load_dotenv(os.path.join(simulator_dir, "..", ".env"))
		load_dotenv(".env")
		minio_host = os.getenv("MINIO_HOST_BIND", "localhost")
		if minio_host in {"0.0.0.0", "127.0.0.1"}:
			minio_host = "localhost"
		minio_endpoint = os.getenv(
			"MINIO_ENDPOINT",
			f"{minio_host}:{os.getenv('MINIO_API_HOST_PORT', '9000')}",
		)
		return cls(
			tracking_api_url=os.getenv("TRACKING_API_URL", DEFAULT_TRACKING_API_URL),
			data_source_id=UUID(os.getenv("TRACKING_DATA_SOURCE_ID", DEFAULT_DATA_SOURCE_ID)),
			openai_api_key=os.getenv("LEO_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"),
			openai_model=(
				os.getenv("LEO_OPENAI_MODEL_NAME")
				or os.getenv("OPENAI_MODEL")
				or DEFAULT_OPENAI_MODEL
			),
			openai_base_url=os.getenv("LEO_OPENAI_BASE_URL") or os.getenv("OPENAI_BASE_URL"),
			openai_reasoning_effort=os.getenv("OPENAI_REASONING_EFFORT", "none"),
			request_timeout_seconds=float(os.getenv("TRACKING_REQUEST_TIMEOUT_SECONDS", "10")),
			max_steps=int(os.getenv("WEB_SIMULATOR_MAX_STEPS", "8")),
			offline=os.getenv("WEB_SIMULATOR_OFFLINE", "false").lower() in {"1", "true", "yes"},
			minio_endpoint=minio_endpoint.removeprefix("http://").removeprefix("https://").rstrip("/"),
			minio_access_key=os.getenv("MINIO_ROOT_USER") or os.getenv("S3_ACCESS_KEY_ID"),
			minio_secret_key=os.getenv("MINIO_ROOT_PASSWORD") or os.getenv("S3_SECRET_ACCESS_KEY"),
			minio_secure=os.getenv("MINIO_SECURE", "false").lower() in {"1", "true", "yes"},
			s3_verify_wait_seconds=float(os.getenv("TRACKING_S3_VERIFY_WAIT_SECONDS", "5")),
			s3_verify_retry_seconds=float(os.getenv("TRACKING_S3_VERIFY_RETRY_SECONDS", "2")),
			s3_verify_attempts=int(os.getenv("TRACKING_S3_VERIFY_ATTEMPTS", "3")),
			verify_s3=os.getenv("TRACKING_S3_VERIFY_ENABLED", "true").lower()
			in {"1", "true", "yes"},
		)


@dataclass(frozen=True)
class UserProfile:
	"""Synthetic identity and preferences supplied to the AI agent."""

	user_id: str
	session_id: str
	name: str
	budget_vnd: int
	preferred_category: str


class TrackingLogClient:
	"""Small standard-library client for the tracking ingestion endpoint."""

	def __init__(self, endpoint: str, timeout_seconds: float = 10.0):
		self.endpoint = endpoint
		self.timeout_seconds = timeout_seconds

	def send(
		self,
		*,
		data_source_id: UUID,
		session_id: str,
		user_id: str,
		events: list[dict[str, Any]],
	) -> dict[str, Any]:
		"""Send one user's ordered event batch to the tracking API."""
		payload = {
			"data_source_id": str(data_source_id),
			"session_id": session_id,
			"user_id": user_id,
			"events": events,
		}
		request = urllib.request.Request(
			self.endpoint,
			data=json.dumps(payload).encode("utf-8"),
			headers={
				"Accept": "application/json",
				"Content-Type": "application/json",
				"User-Agent": "leo-web-user-simulator/1.0",
			},
			method="POST",
		)
		try:
			with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
				response_body = response.read().decode("utf-8")
		except urllib.error.HTTPError as exc:
			detail = exc.read().decode("utf-8", errors="replace")
			raise TrackingApiError(f"tracking API returned HTTP {exc.code}: {detail}") from exc
		except urllib.error.URLError as exc:
			raise TrackingApiError(f"tracking API is unavailable: {exc.reason}") from exc

		try:
			return json.loads(response_body)
		except json.JSONDecodeError as exc:
			raise TrackingApiError("tracking API returned invalid JSON") from exc


class MinioTrackingVerifier:
	"""Read and validate tracking objects from the local MinIO instance."""

	def __init__(
		self,
		endpoint: str,
		access_key: str | None,
		secret_key: str | None,
		secure: bool = False,
	):
		if not access_key or not secret_key:
			raise TrackingVerificationError(
				"MinIO credentials missing; set MINIO_ROOT_USER and MINIO_ROOT_PASSWORD"
			)
		try:
			minio_module = importlib.import_module("minio")
		except ImportError as exc:
			raise TrackingVerificationError(
				"Install the simulator dependencies before reading MinIO"
			) from exc
		self.client = minio_module.Minio(
			endpoint,
			access_key=access_key,
			secret_key=secret_key,
			secure=secure,
		)

	def read_records(self, bucket: str, object_key: str) -> list[dict[str, Any]]:
		"""Read the tracking object's newline-delimited JSON records."""
		try:
			response = self.client.get_object(bucket, object_key)
			try:
				body = response.read()
			finally:
				response.close()
				response.release_conn()
		except Exception as exc:
			raise TrackingVerificationError(
				f"Could not read s3://{bucket}/{object_key} from MinIO: {exc}"
			) from exc

		records: list[dict[str, Any]] = []
		for line_number, line in enumerate(body.decode("utf-8").splitlines(), start=1):
			if not line.strip():
				continue
			try:
				record = json.loads(line)
			except json.JSONDecodeError as exc:
				raise TrackingVerificationError(
					f"Invalid JSON in s3://{bucket}/{object_key} line {line_number}"
				) from exc
			if not isinstance(record, dict):
				raise TrackingVerificationError(
					f"Expected an object in s3://{bucket}/{object_key} line {line_number}"
				)
			records.append(record)
		return records

	def verify_batch(
		self,
		*,
		bucket: str,
		object_key: str,
		data_source_id: UUID,
		session_id: str | None,
		user_id: str | None,
		expected_events: list[dict[str, Any]],
	) -> list[dict[str, Any]]:
		"""Assert that MinIO contains exactly the batch sent to the API."""
		records = self.read_records(bucket, object_key)
		if len(records) != len(expected_events):
			raise TrackingVerificationError(
				f"Expected {len(expected_events)} records but found {len(records)} "
				f"in s3://{bucket}/{object_key}"
			)

		expected_source = str(data_source_id)
		for index, (record, expected_event) in enumerate(zip(records, expected_events), start=1):
			if record.get("data_source_id") != expected_source:
				raise TrackingVerificationError(
					f"Record {index} has the wrong data_source_id in "
					f"s3://{bucket}/{object_key}"
				)
			expected_stored_event = dict(expected_event)
			if session_id:
				expected_stored_event.setdefault("session_id", session_id)
			if user_id:
				expected_stored_event.setdefault("user_id", user_id)
			if record.get("event") != expected_stored_event:
				raise TrackingVerificationError(
					f"Record {index} does not match the event sent to "
					f"s3://{bucket}/{object_key}"
				)
		return records


class WebUserAgent:
	"""Execute an ecommerce journey and turn each action into a tracking event."""

	def __init__(
		self,
		profile: UserProfile,
		config: AgentConfig,
		*,
		rng: random.Random | None = None,
		openai_client: Any | None = None,
	):
		self.profile = profile
		self.config = config
		self.rng = rng or random.Random()
		self.openai_client = openai_client
		self.events: list[dict[str, Any]] = []
		self.current_product_id: str | None = None
		self.seen_ad_id: str | None = None
		self._viewed_product = False
		self._asked_question = False

	def run(self) -> list[dict[str, Any]]:
		"""Run either the model-directed or deterministic user journey."""
		if self.config.offline or self.openai_client is None:
			return self._run_offline()
		return self._run_with_model()

	def _run_offline(self) -> list[dict[str, Any]]:
		"""Run a predictable journey for local development and CI."""
		ad = self.rng.choice(AD_CATALOG)
		self._execute_action("see_ad", ad)
		self._execute_action("view_product", {"product_id": ad["product_id"]})
		if self.rng.choice((True, False)):
			self._execute_action("ask_price", {"product_id": ad["product_id"]})
		else:
			self._execute_action(
				"ask_support",
				{
					"product_id": ad["product_id"],
					"question": "Is this product available for delivery this week?",
				},
			)
		self._execute_action(
			"purchase_product",
			{"product_id": ad["product_id"], "quantity": 1},
		)
		return self.events

	def _run_with_model(self) -> list[dict[str, Any]]:
		"""Let the OpenAI-compatible model select tools until purchase or stop."""
		messages: list[dict[str, Any]] = [
			{"role": "system", "content": self._system_prompt()},
			{
				"role": "user",
				"content": (
					f"You are {self.profile.name}. Explore the shop as a real customer, "
					"then complete a purchase if the product fits your needs."
				),
			},
		]
		client = self.openai_client
		if client is None:
			raise RuntimeError("OpenAI client is required for model-directed journeys")

		for _ in range(self.config.max_steps):
			response = client.chat.completions.create(
				model=self.config.openai_model,
				messages=messages,
				tools=TOOL_DEFINITIONS,
				tool_choice="required",
				reasoning_effort=self.config.openai_reasoning_effort,
				temperature=0.7,
			)
			assistant_message = response.choices[0].message
			tool_calls = assistant_message.tool_calls or []
			if not tool_calls:
				LOGGER.warning("Model returned no action for user %s", self.profile.user_id)
				break

			messages.append(
				{
					"role": "assistant",
					"content": assistant_message.content,
					"tool_calls": [call.model_dump() for call in tool_calls],
				}
			)
			for tool_call in tool_calls:
				try:
					arguments = json.loads(tool_call.function.arguments or "{}")
					result = self._execute_action(tool_call.function.name, arguments)
				except (ValueError, KeyError, json.JSONDecodeError) as exc:
					result = {"error": str(exc)}
				messages.append(
					{
						"role": "tool",
						"tool_call_id": tool_call.id,
						"content": json.dumps(result),
					}
				)
				if result.get("event_name") == "purchase":
					return self.events

		LOGGER.warning(
			"User %s ended without a purchase after %s steps",
			self.profile.user_id,
			self.config.max_steps,
		)
		return self.events

	def _system_prompt(self) -> str:
		catalog = json.dumps(
			{"ads": AD_CATALOG, "products": PRODUCT_CATALOG},
			ensure_ascii=True,
		)
		return (
			"You are a realistic ecommerce customer simulator. Use only the supplied tools. "
			"Start by seeing an ad, view that product, ask either for its price or for support, "
			"and then purchase it. Never invent product IDs. Choose a product within the user's "
			f"budget when possible. The user profile is {json.dumps(self.profile.__dict__)}. "
			f"The shop catalog is {catalog}"
		)

	def _execute_action(self, action: str, arguments: dict[str, Any]) -> dict[str, Any]:
		handlers = {
			"see_ad": self._see_ad,
			"view_product": self._view_product,
			"ask_price": self._ask_price,
			"ask_support": self._ask_support,
			"purchase_product": self._purchase_product,
		}
		if action not in handlers:
			raise ValueError(f"Unsupported ecommerce action: {action}")
		return handlers[action](arguments)

	def _see_ad(self, arguments: dict[str, Any]) -> dict[str, Any]:
		ad = _find_by_id(AD_CATALOG, arguments.get("ad_id")) or AD_CATALOG[0]
		product = _require_product(ad["product_id"])
		self.seen_ad_id = ad["ad_id"]
		self.current_product_id = product["product_id"]
		return self._record_event(
			"ad_impression",
			{
				"ad_id": ad["ad_id"],
				"campaign": ad["campaign"],
				"placement": ad["placement"],
				"product_id": product["product_id"],
				"product_name": product["name"],
			},
			page_url=f"https://shop.example.test/ads/{ad['ad_id']}",
		)

	def _view_product(self, arguments: dict[str, Any]) -> dict[str, Any]:
		product = self._product_from_arguments(arguments)
		self.current_product_id = product["product_id"]
		self._viewed_product = True
		return self._record_event(
			"product_view",
			{"product_id": product["product_id"], "product_name": product["name"]},
			page_url=f"https://shop.example.test/products/{product['product_id']}",
		)

	def _ask_price(self, arguments: dict[str, Any]) -> dict[str, Any]:
		product = self._product_from_arguments(arguments)
		self._asked_question = True
		return self._record_event(
			"price_request",
			{
				"product_id": product["product_id"],
				"question": "What is the current price?",
				"answer_price_vnd": product["price_vnd"],
			},
			page_url=f"https://shop.example.test/products/{product['product_id']}",
		)

	def _ask_support(self, arguments: dict[str, Any]) -> dict[str, Any]:
		product = self._product_from_arguments(arguments)
		question = str(arguments.get("question") or "Can you tell me more about this product?")[:500]
		self._asked_question = True
		return self._record_event(
			"support_request",
			{
				"product_id": product["product_id"],
				"question": question,
				"answer": "Yes, this item is in stock and can be delivered within three business days.",
			},
			page_url="https://shop.example.test/support",
		)

	def _purchase_product(self, arguments: dict[str, Any]) -> dict[str, Any]:
		product = self._product_from_arguments(arguments)
		quantity = max(1, min(int(arguments.get("quantity", 1)), 2))
		total_price_vnd = product["price_vnd"] * quantity
		result = self._record_event(
			"purchase",
			{
				"product_id": product["product_id"],
				"product_name": product["name"],
				"quantity": quantity,
				"total_price_vnd": total_price_vnd,
				"order_id": f"order-{uuid4().hex[:12]}",
				"currency": "VND",
			},
			page_url="https://shop.example.test/checkout/complete",
		)
		result["order_status"] = "confirmed"
		return result

	def _product_from_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
		product_id = arguments.get("product_id") or self.current_product_id
		return _require_product(product_id)

	def _record_event(
		self,
		event_name: str,
		properties: dict[str, Any],
		*,
		page_url: str,
	) -> dict[str, Any]:
		event = {
			"event_name": event_name,
			"occurred_at": datetime.now(timezone.utc).isoformat(),
			"page_url": page_url,
			"source": "web_user_simulator",
			"agent_mode": "offline" if self.config.offline else "openai",
			"model": self.config.openai_model,
			**properties,
		}
		self.events.append(event)
		return {"event_name": event_name, "status": "recorded", **properties}


def _find_by_id(items: tuple[dict[str, Any], ...], item_id: Any) -> dict[str, Any] | None:
	return next((item for item in items if item.get("product_id") == item_id or item.get("ad_id") == item_id), None)


def _require_product(product_id: Any) -> dict[str, Any]:
	product = _find_by_id(PRODUCT_CATALOG, product_id)
	if product is None:
		raise ValueError(f"Unknown product_id: {product_id}")
	return product


def _create_openai_client(config: AgentConfig) -> Any | None:
	if config.offline or not config.openai_api_key:
		return None
	try:
		openai_module = importlib.import_module("openai")
	except ImportError as exc:
		raise RuntimeError("Install the simulator dependencies before using OpenAI mode") from exc

	client_options: dict[str, Any] = {"api_key": config.openai_api_key}
	if config.openai_base_url:
		client_options["base_url"] = config.openai_base_url
	return openai_module.OpenAI(**client_options)


def _build_profile(index: int, rng: random.Random) -> UserProfile:
	names = ("Mai Nguyen", "An Tran", "Linh Pham", "Minh Le", "Ha Vo")
	categories = tuple(product["category"] for product in PRODUCT_CATALOG)
	return UserProfile(
		user_id=f"web-user-{index:04d}-{uuid4().hex[:8]}",
		session_id=f"web-session-{uuid4().hex}",
		name=rng.choice(names),
		budget_vnd=rng.randrange(1_000_000, 2_400_001, 100_000),
		preferred_category=rng.choice(categories),
	)


def run_simulation(config: AgentConfig, user_count: int, *, seed: int | None = None, dry_run: bool = False) -> int:
	"""Run users, send their event batches, and return a process exit code."""
	rng = random.Random(seed)
	tracker = TrackingLogClient(config.tracking_api_url, config.request_timeout_seconds)
	client = _create_openai_client(config)
	verifier = None
	if config.verify_s3 and not dry_run:
		verifier = MinioTrackingVerifier(
			config.minio_endpoint,
			config.minio_access_key,
			config.minio_secret_key,
			config.minio_secure,
		)
	if client is None and not config.offline:
		LOGGER.info("No OpenAI key configured; using offline ecommerce journeys")
		config = replace(config, offline=True)

	failed = 0
	for index in range(1, user_count + 1):
		profile = _build_profile(index, rng)
		agent = WebUserAgent(profile, config, rng=rng, openai_client=client)
		events = agent.run()
		if dry_run:
			LOGGER.info("Dry run %s: %s", profile.user_id, json.dumps(events))
			continue
		try:
			response = tracker.send(
				data_source_id=config.data_source_id,
				session_id=profile.session_id,
				user_id=profile.user_id,
				events=events,
			)
			stored_records = None
			if verifier is not None:
				LOGGER.info(
					"Waiting %.1f seconds before verifying %s in MinIO",
					config.s3_verify_wait_seconds,
					profile.user_id,
				)
				time.sleep(config.s3_verify_wait_seconds)
				for attempt in range(1, config.s3_verify_attempts + 1):
					try:
						stored_records = verifier.verify_batch(
							bucket=response["bucket"],
							object_key=response["object_key"],
							data_source_id=config.data_source_id,
							session_id=profile.session_id,
							user_id=profile.user_id,
							expected_events=events,
						)
						break
					except TrackingVerificationError:
						if attempt == config.s3_verify_attempts:
							raise
						LOGGER.warning(
							"MinIO verification attempt %s/%s failed for %s; retrying in %.1f seconds",
							attempt,
							config.s3_verify_attempts,
							profile.user_id,
							config.s3_verify_retry_seconds,
						)
						time.sleep(config.s3_verify_retry_seconds)
			LOGGER.info(
				"Sent %s events for %s (%s)",
				len(events),
				profile.user_id,
				response.get("object_key", "tracking API accepted"),
			)
			if stored_records is not None:
				print(
					json.dumps(
						{
							"user_id": profile.user_id,
							"session_id": profile.session_id,
							"bucket": response["bucket"],
							"object_key": response["object_key"],
							"verified_event_count": len(stored_records),
							"stored_events": [record["event"] for record in stored_records],
						},
						ensure_ascii=False,
						indent=2,
					)
				)
		except (TrackingApiError, TrackingVerificationError, KeyError) as exc:
			failed += 1
			LOGGER.error("Could not send or verify events for %s: %s", profile.user_id, exc)
	return 1 if failed else 0


def _parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--users", type=int, default=int(os.getenv("WEB_SIMULATOR_USERS", "1")))
	parser.add_argument("--seed", type=int, default=None)
	parser.add_argument("--model", default=None, help="Override LEO_OPENAI_MODEL_NAME")
	parser.add_argument("--tracking-url", default=None, help="Override TRACKING_API_URL")
	parser.add_argument("--s3-wait-seconds", type=float, default=None)
	parser.add_argument("--no-s3-verify", action="store_true", help="Skip the MinIO read-back check")
	parser.add_argument("--offline", action="store_true", help="Do not call the OpenAI model")
	parser.add_argument("--dry-run", action="store_true", help="Print generated events without calling the API")
	parser.add_argument("--verbose", action="store_true")
	return parser.parse_args()


def main() -> int:
	args = _parse_args()
	if args.users < 1:
		raise SystemExit("--users must be at least 1")
	config = AgentConfig.from_environment()
	if args.model:
		config = replace(config, openai_model=args.model)
	if args.tracking_url:
		config = replace(config, tracking_api_url=args.tracking_url)
	if args.s3_wait_seconds is not None:
		if args.s3_wait_seconds < 0:
			raise SystemExit("--s3-wait-seconds must be zero or greater")
		config = replace(config, s3_verify_wait_seconds=args.s3_wait_seconds)
	if args.no_s3_verify:
		config = replace(config, verify_s3=False)
	if args.offline:
		config = replace(config, offline=True)
	logging.basicConfig(
		level=logging.INFO if args.verbose or args.dry_run else logging.WARNING,
		format="%(asctime)s %(levelname)s %(message)s",
	)
	return run_simulation(config, args.users, seed=args.seed, dry_run=args.dry_run)


if __name__ == "__main__":
	raise SystemExit(main())
