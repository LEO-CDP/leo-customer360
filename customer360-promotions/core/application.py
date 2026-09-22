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
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from core.config import db_promotions_engine, promotions_settings
from repository.ad_repository import AdRepository
from repository.placement_repository import PlacementRepository


logger = logging.getLogger(__name__)


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

    async def _shutdown(self) -> None:
        """
        Application shutdown.

        Later:

        - close Redis
        - flush Kafka producer
        - close external providers
        """

        logger.info("Customer 360 Promotions API shutting down")

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
        tenant: str = "demo",
        limit: int = 5,
    ) -> dict:
        """
        Assemble a full ad-serving payload (creative, rendering, tracking,
        advertiser, destination) for a placement, or for a single ad when
        `placement_ref` matches an `ad_key` instead.

        Dev/test endpoint powering html/ads-banner.html and
        html/ads.loader.js against real leo_ads data instead of the
        static html/ads.data.json fixture.
        """

        ads = self.ad_repository.get_serving_ads(
            tenant_key=tenant,
            placement_ref=placement_ref,
            limit=limit,
        )

        return {"ads": ads}
