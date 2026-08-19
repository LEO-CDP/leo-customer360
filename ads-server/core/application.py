"""
Application composition root for the LEO Ad Server.

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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from core.config import ad_server_settings, db_ad_engine
from repository.ad_repository import AdRepository
from repository.placement_repository import PlacementRepository


logger = logging.getLogger(__name__)


class AdServerApplication:
    """
    Main application container.

    This class is intentionally lightweight. It is the composition root of
    the application and should not contain business logic.
    """

    def __init__(self) -> None:
        self.engine = db_ad_engine

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
        logger.info("LEO Ad Server API starting")

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
            "LEO Ad Server version=%s starting",
            ad_server_settings.api_version,
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

        logger.info("LEO Ad Server API shutting down")

        self.engine.dispose()

    # ------------------------------------------------------------------
    # Infrastructure
    # ------------------------------------------------------------------

    def _check_database(self) -> None:
        """
        Validate that PostgreSQL is reachable.
        """

        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))

            logger.info("PostgreSQL connection OK")

        except Exception:
            logger.exception("PostgreSQL connection failed")
            raise

    # ------------------------------------------------------------------
    # FastAPI application
    # ------------------------------------------------------------------

    def create_app(self) -> FastAPI:
        """
        Create and configure the FastAPI application.
        """

        application = FastAPI(
            title="LEO Ad Server API",
            description=(
                "High-scale multi-source Ad Server API backed by "
                "the PostgreSQL leo_ads schema."
            ),
            version=ad_server_settings.api_version,
            lifespan=self.lifespan,
            # Public mount point when fronted by a path-routing proxy (e.g. Caddy /ads).
            # Keeps generated URLs (Swagger's openapi.json, redirects) prefixed correctly.
            # Empty by default (served at root, e.g. a dedicated prod host).
            root_path=os.getenv("ADS_ROOT_PATH", ""),
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
            "service": "leo-ad-server-api",
            "status": "ok",
            "version": ad_server_settings.api_version,
            "schema": "leo_ads",
            "docs": "/docs",
        }

    def health(self) -> dict:
        """
        General health endpoint.

        This intentionally remains cheap.
        """

        return {
            "status": "ok",
            "service": "leo-ad-server-api",
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
