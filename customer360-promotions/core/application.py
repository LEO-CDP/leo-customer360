"""
Application composition root for the c360 Promotions.

Responsibilities:

- create FastAPI application
- configure middleware
- configure application lifecycle
- initialize repositories
- expose health endpoints
- register routers

The application object intentionally owns dependency wiring so that later
we can introduce:

    AdService
    TargetingService
    RankingService
    CreativeResolver
    RedisRepository
    KafkaProducer

without changing app.py.
"""

from __future__ import annotations

import logging
import os
import re
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Literal, Optional

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from core.config import db_promotions_engine, promotions_settings
from core.schemas import ConsentChange, EventBatch
from personalization.buffered_writer import BufferedWriter
from personalization.conversions import ConversionError, ConversionService, issue_click_id
from personalization.decision_log import DecisionLog
from personalization.eligibility import build_context
from personalization.event_ingest import (
    EventBatchTooLarge,
    EventIngest,
    EventRateLimited,
    IngestUnavailable,
)
from personalization.hot_state import HotState, HotStateUnavailable
from personalization.identity import (
    VisitorKeyUnavailable,
    describe_error,
    fingerprint,
    visitor_key,
)
from personalization.ranking_service import RankingService
from personalization.settings import SettingsResolver
from repository.ad_repository import AdRepository
from repository.consent_repository import ConsentRepository
from repository.event_repository import EventRepository
from repository.placement_repository import PlacementRepository
from repository.profile_repository import ProfileRepository
from repository.setting_repository import SettingRepository
from repository.stats_repository import StatsRepository
from repository.tenant_repository import TenantRepository


logger = logging.getLogger(__name__)


class RedactVisitorIds(logging.Filter):
    """Replace the value of any ``leovid=`` in a log line.

    ``GET /serve`` carries the widget's visitor id as a query parameter (the
    contract), so the server's own access log line would otherwise contain it.
    No log line may hold a raw visitor id (T097).
    """

    _pattern = re.compile(r"(leovid=)[^&\s\"']+")

    def _clean(self, value):
        return self._pattern.sub(r"\1[redacted]", value) if isinstance(value, str) else value

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._clean(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(self._clean(arg) for arg in record.args)
        return True


def _install_log_redaction() -> None:
    """Attach the filter to the server's access logger, once."""

    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactVisitorIds) for f in access_logger.filters):
        access_logger.addFilter(RedactVisitorIds())


class PromotionsApplication:
    """
    Main application container.

    This class is intentionally lightweight. It is the composition root of
    the application and should not contain business logic.
    """

    def __init__(self) -> None:
        self.engine = db_promotions_engine

        self.ad_repository = AdRepository(
            engine=self.engine,
        )

        self.placement_repository = PlacementRepository(
            engine=self.engine,
        )

        # Ad personalisation (specs/003-ad-personalization-engine). Redis is a
        # rebuildable cache that fails open: nothing here is allowed to fail a
        # request just because Redis or a settings read is unavailable.
        self.hot_state = HotState.from_settings(promotions_settings)
        self._now = time.time  # wall clock; replaced in tests for deterministic windows
        self.tenant_repository = TenantRepository(engine=self.engine)
        self.setting_repository = SettingRepository(engine=self.engine)
        self.consent_repository = ConsentRepository(engine=self.engine)
        self.settings_resolver = SettingsResolver(
            repository=self.setting_repository,
            hot_state=self.hot_state,
            ttl_seconds=promotions_settings.settings_cache_ttl_seconds,
        )
        self.profile_repository = ProfileRepository(engine=self.engine)
        self.stats_repository = StatsRepository(engine=self.engine)
        self.event_repository = EventRepository(engine=self.engine)

        # Durable writes are batched off the request path. The writers are created
        # here but only started in `_startup`, and drained in `_shutdown`.
        self.event_writer = BufferedWriter(
            self.event_repository.insert_events,
            max_size=promotions_settings.event_buffer_max,
            name="ad-event-writer",
            start=False,
        )
        self.decision_writer = BufferedWriter(
            self.event_repository.insert_decisions,
            max_size=promotions_settings.event_buffer_max,
            name="serving-decision-writer",
            start=False,
        )
        self.decision_log = DecisionLog(self.decision_writer)

    def _ranking_service(self) -> RankingService:
        """Built per call from the current collaborators (cheap; keeps tests able to swap them)."""

        return RankingService(
            ad_repository=self.ad_repository,
            settings_resolver=self.settings_resolver,
            consent_repository=self.consent_repository,
            profile_repository=self.profile_repository,
            stats_repository=self.stats_repository,
            hot_state=self.hot_state,
            decisions=self.decision_log,
            config=promotions_settings,
        )

    def _event_ingest(self) -> EventIngest:
        return EventIngest(
            ad_repository=self.ad_repository,
            consent_repository=self.consent_repository,
            settings_resolver=self.settings_resolver,
            hot_state=self.hot_state,
            writer=self.event_writer,
            config=promotions_settings,
            wall_clock=self._now,
        )

    def _conversions(self) -> ConversionService:
        return ConversionService(
            tenant_repository=self.tenant_repository,
            event_repository=self.event_repository,
            hot_state=self.hot_state,
            writer=self.event_writer,
            config=promotions_settings,
        )

    # ------------------------------------------------------------------
    # FastAPI lifecycle
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def lifespan(
        self,
        application: FastAPI,
    ) -> AsyncIterator[None]:
        logger.info("Customer 360 Promotions API starting")

        await self._startup()

        try:
            yield
        finally:
            await self._shutdown()

    async def _startup(self) -> None:
        """
        Application startup.

        Keep this method small.

        Later this is the correct place for:

        - Redis connection initialization
        - Kafka producer initialization
        - model/config loading
        - serving-index warmup
        - targeting engine initialization
        """

        logger.info(
            "Customer 360 Promotions version=%s starting",
            promotions_settings.api_version,
        )

        self._check_database()

        self.event_writer.start()
        self.decision_writer.start()
        self._warm_up()

        # Not fatal: without Redis, serving simply uses default ranking.
        if not self.hot_state.ping():
            logger.warning(
                "Redis is not reachable; personalisation will fall back to default ranking"
            )
        if promotions_settings.effective_visitor_key_secret is None:
            logger.warning(
                "visitor_key_master_secret is not set; personalisation is disabled "
                "and visitor consent cannot be recorded"
            )

    def _warm_up(self) -> None:
        """Run the two budgeted reads once so the first real request is not a cold one.

        A new database connection pays for catalogue lookups on its first query,
        which can exceed the personalisation time budget and send that request to
        default ranking. Best effort: a failure here changes nothing but warmth.
        """

        try:
            self.profile_repository.get_profile(0, [b"\x00" * 16])
            self.stats_repository.get_ad_stats(0, [0])
        except Exception:  # noqa: BLE001
            logger.warning("personalisation warm-up failed; the first requests may be cold")

    async def _shutdown(self) -> None:
        """
        Application shutdown.

        Later:

        - close Redis
        - flush Kafka producer
        - close external providers
        """

        logger.info("Customer 360 Promotions API shutting down")

        # Drain buffered events and decisions before the connection pool goes away.
        self.event_writer.close()
        self.decision_writer.close()
        self.engine.dispose()

    # ------------------------------------------------------------------
    # Infrastructure
    # ------------------------------------------------------------------

    def _database_reachable(self) -> bool:
        """
        Graceful PostgreSQL connectivity probe: returns a bool instead of
        raising, so the /health endpoint can answer 503 with a descriptive body
        rather than an unhandled 500. Single source of truth for "can we reach
        the DB" — `_check_database` (startup) wraps this and raises.
        """

        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return True
        except Exception:
            logger.exception("PostgreSQL connection failed")
            return False

    def _check_database(self) -> None:
        """
        Strict startup check: raise if PostgreSQL is unreachable.
        """

        if not self._database_reachable():
            raise RuntimeError("PostgreSQL is not reachable")

        logger.info("PostgreSQL connection OK")

    # ------------------------------------------------------------------
    # FastAPI application
    # ------------------------------------------------------------------

    def create_app(self) -> FastAPI:
        """
        Create and configure the FastAPI application.
        """

        application = FastAPI(
            title="Customer 360 Promotions API",
            description=(
                "High-scale multi-source Promotions API backed by "
                "the PostgreSQL leo_ads schema."
            ),
            version=promotions_settings.api_version,
            lifespan=self.lifespan,
            # Public mount point when fronted by a path-routing proxy (e.g. Caddy /ads).
            # Keeps generated URLs (Swagger's openapi.json, redirects) prefixed correctly.
            # Empty by default (served at root, e.g. a dedicated prod host).
            root_path=os.getenv("c360_PROMOTION_ROOT_PATH", ""),
        )

        _install_log_redaction()
        self._configure_middleware(application)
        self._register_routes(application)

        return application

    def _configure_middleware(
        self,
        application: FastAPI,
    ) -> None:
        """
        Configure HTTP middleware.
        """

        application.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def _register_routes(
        self,
        application: FastAPI,
    ) -> None:
        """
        Register API endpoints.

        Keep route registration here so app.py remains tiny.
        """

        application.add_api_route(
            "/",
            self.root,
            methods=["GET"],
            tags=["Health"],
        )

        application.add_api_route(
            "/health",
            self.health,
            methods=["GET"],
            tags=["Health"],
        )

        application.add_api_route(
            "/health/database",
            self.database_health,
            methods=["GET"],
            tags=["Health"],
        )

        application.add_api_route(
            "/ads/{ad_id}",
            self.get_ad,
            methods=["GET"],
            tags=["Ads"],
        )

        application.add_api_route(
            "/placements/{placement_key}",
            self.get_placement,
            methods=["GET"],
            tags=["Placements"],
        )

        application.add_api_route(
            "/serve/{placement_ref}",
            self.serve_ads,
            methods=["GET"],
            tags=["Ads"],
        )

        application.add_api_route(
            "/events",
            self.post_events,
            methods=["POST"],
            status_code=202,
            tags=["Events"],
        )

        application.add_api_route(
            "/conversions",
            self.post_conversions,
            methods=["POST"],
            status_code=202,
            tags=["Events"],
        )

        application.add_api_route(
            "/consent",
            self.put_consent,
            methods=["PUT"],
            status_code=204,
            response_class=Response,
            tags=["Consent"],
        )

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def root(self) -> dict:
        """
        Basic service information.
        """

        return {
            "service": "customer360-promotions",
            "status": "ok",
            "version": promotions_settings.api_version,
            "schema": "leo_ads",
            "docs": "/docs",
        }

    def health(self, response: Response) -> dict:
        """
        Readiness health endpoint: verifies the PostgreSQL dependency is
        reachable. Returns 503 (with database=unreachable) when it is not, so
        the deploy health-gate and Docker healthcheck don't mark the ad server
        healthy while it cannot serve. (Redis is not wired into the ad server
        yet, so there is nothing else to probe.)
        """

        db_ok = self._database_reachable()
        if not db_ok:
            response.status_code = 503

        return {
            "status": "ok" if db_ok else "error",
            "service": "customer360-promotions",
            "database": "reachable" if db_ok else "unreachable",
        }

    def database_health(self) -> dict:
        """
        PostgreSQL health endpoint.
        """

        self._check_database()

        return {
            "status": "ok",
            "database": "reachable",
            "schema": "leo_ads",
        }

    # ------------------------------------------------------------------
    # Temporary read endpoints
    # ------------------------------------------------------------------

    def get_ad(
        self,
        ad_id: int,
    ):
        """
        Retrieve an ad by ID.

        This is deliberately implemented through a repository instead of
        writing SQL in the API layer.
        """

        return self.ad_repository.get_by_id(ad_id)

    def get_placement(
        self,
        placement_key: str,
    ):
        """
        Retrieve an active placement.
        """

        return self.placement_repository.get_active_by_key(
            placement_key=placement_key,
        )

    def serve_ads(
        self,
        placement_ref: str,
        request: Request,
        tenant: str = "demo",
        limit: int = Query(5, ge=1, le=20),
        leovid: Optional[str] = Query(None, max_length=128),
        session: Optional[str] = Query(None, max_length=128),
        site: Optional[str] = Query(None, max_length=128),
        consent: Literal["granted", "denied", "unknown"] = "unknown",
        ctx: Optional[str] = Query(None, max_length=512),
        lang: Optional[str] = Query(None, max_length=16),
    ) -> dict:
        """
        Assemble a full ad-serving payload (creative, rendering, tracking,
        advertiser, destination) for a placement, or for a single ad when
        `placement_ref` matches an `ad_key` instead.

        Every request runs eligibility; when personalisation applies to the
        visitor the eligible ads are ranked for them, otherwise they come back in
        the default order. The response also carries a `request_id` (echoed by
        later events) and a `personalisation` object saying what happened.
        Everything except the candidate lookup fails open to default ranking.

        `session` is accepted for the widget's benefit and not used for ranking.
        """

        result = self._ranking_service().rank(
            tenant_key=tenant,
            placement_ref=placement_ref,
            limit=limit,
            leovid=leovid,
            site=site,
            consent=consent if consent != "unknown" else None,
            context=build_context(dict(request.headers), language=lang, keywords=ctx),
        )

        if consent != "unknown":
            self._record_reported_consent(result.tenant_id, leovid, consent)

        master_secret = promotions_settings.effective_visitor_key_secret
        def build() -> list:
            return self.ad_repository.build_serving_payloads(
                result.placement,
                result.ads,
                placement_ref,
                click_id_for=lambda ad: issue_click_id(
                    master_secret, ad["tenant_id"], result.request_id, ad["ad_id"]
                ),
            )

        try:
            ads = build()
        except OperationalError as exc:
            # Under heavy load a pooled connection can fail a health check or time out
            # once. Every request must still return a valid result, so try once more
            # on a fresh checkout before letting the error out.
            logger.warning("serve: payload build failed once (%s); retrying", describe_error(exc))
            ads = build()

        return {
            "request_id": result.request_id,
            "personalisation": {
                "applied": result.applied,
                "group": result.group_code,
                "reason": result.reason,
            },
            "ads": ads,
        }

    async def post_conversions(self, request: Request) -> dict:
        """
        POST /conversions: a signed server-to-server conversion postback.

        The signature covers the raw body, so the body is read untouched and
        validated only after it verifies. 401 for a bad signature or stale
        timestamp, 409 for a repeated event_id, 422 when it cannot be attributed,
        503 when postbacks are not enabled (or intake is busy).
        """

        raw_body = await request.body()
        try:
            return await run_in_threadpool(
                self._conversions().process,
                tenant_key=request.headers.get("x-tenant-key"),
                timestamp=request.headers.get("x-timestamp"),
                signature=request.headers.get("x-signature"),
                raw_body=raw_body,
            )
        except ConversionError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail, headers=exc.headers)

    def _record_reported_consent(
        self, tenant_id: Optional[int], leovid: Optional[str], status: str
    ) -> None:
        """Store the consent state the site reported on /serve. Never fails the request."""

        if tenant_id is None or not leovid:
            return
        try:
            user_key = visitor_key(tenant_id, leovid)
            if self.consent_repository.has_open_erasure(tenant_id, user_key):
                return  # never re-create consent data for a visitor being erased
            self.consent_repository.record(tenant_id, user_key, status, "site_param")
            if status == "denied":
                self.hot_state.purge_visitor(tenant_id, user_key)
        except (VisitorKeyUnavailable, ValueError, HotStateUnavailable):
            return
        except Exception as exc:  # noqa: BLE001 - recording a choice must not fail an ad request
            logger.error("serve: could not record the reported consent (%s)", describe_error(exc))

    def post_events(self, body: EventBatch, request: Request) -> dict:
        """
        POST /events: accept a batch of visitor ad interactions.

        202 with how many events were accepted and why any were rejected. 400 for
        an oversized batch, 404 for an unknown tenant, 429 (with Retry-After) when
        the visitor is over their per-minute limit and 503 (with Retry-After) when
        the durable-write buffer cannot take the batch.
        """

        tenant_id = self.tenant_repository.get_active_tenant_id(body.tenant)
        if tenant_id is None:
            raise HTTPException(status_code=404, detail="Unknown tenant")

        try:
            result = self._event_ingest().ingest(
                tenant_id=tenant_id,
                events=body.events,
                leovid=body.leovid,
                session_id=body.session_id,
                site=body.site,
                user_agent=request.headers.get("user-agent"),
            )
        except EventBatchTooLarge as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except EventRateLimited:
            raise HTTPException(
                status_code=429,
                detail="Too many requests",
                headers={"Retry-After": str(EventRateLimited.retry_after)},
            )
        except IngestUnavailable:
            raise HTTPException(
                status_code=503,
                detail="Event intake is busy; retry shortly",
                headers={"Retry-After": str(IngestUnavailable.retry_after)},
            )

        return result.as_dict()

    # ------------------------------------------------------------------
    # Ad personalisation: consent
    # ------------------------------------------------------------------

    def _check_rate_limit(
        self,
        tenant_id: int,
        user_key: bytes,
        name: str,
        limit_per_minute: int,
    ) -> None:
        """Fixed one-minute window per visitor. Fails open if Redis is unavailable.

        The window number is part of the counter name, so each minute starts a
        fresh counter (the counter TTL slides, so a single counter would never
        reset for a visitor who keeps calling).
        """

        window = int(self._now() // 60)
        try:
            count = self.hot_state.incr_counter(
                tenant_id, user_key, f"rate:{name}:{window}", ttl_seconds=120
            )
        except HotStateUnavailable:
            return

        if count > limit_per_minute:
            raise HTTPException(
                status_code=429,
                detail="Too many requests",
                headers={"Retry-After": "60"},
            )

    def put_consent(self, body: ConsentChange) -> Response:
        """
        PUT /consent: record a visitor's consent choice.

        The visitor is identified by a keyed hash of their visitor id, so only
        the holder of the visitor id can record a choice for it. Repeating the
        current choice is a no-op (204 either way). The choice takes effect for
        ranking on the next request.
        """

        tenant_id = self.tenant_repository.get_active_tenant_id(body.tenant)
        if tenant_id is None:
            raise HTTPException(status_code=404, detail="Unknown tenant")

        try:
            user_key = visitor_key(tenant_id, body.leovid)
        except VisitorKeyUnavailable:
            logger.warning("consent: visitor key secret is not configured")
            raise HTTPException(
                status_code=503, detail="Personalisation is not available"
            )
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid visitor id")

        self._check_rate_limit(
            tenant_id,
            user_key,
            "consent",
            promotions_settings.consent_rate_limit_per_minute,
        )

        changed = self.consent_repository.record(
            tenant_id, user_key, body.status, body.source
        )
        if body.status == "denied":
            # Take effect immediately: drop the visitor's fresh signals and counters
            # now rather than waiting for them to expire. Best effort; the consent
            # record itself already stops ranking and profile building.
            try:
                self.hot_state.purge_visitor(tenant_id, user_key)
            except HotStateUnavailable:
                logger.warning("consent: could not purge hot state for a denied visitor")

        logger.info(
            "consent: tenant=%s visitor=%s status=%s changed=%s",
            tenant_id,
            fingerprint(user_key),
            body.status,
            changed,
        )

        return Response(status_code=204)
