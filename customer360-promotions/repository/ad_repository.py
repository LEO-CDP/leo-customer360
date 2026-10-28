"""
Ad repository.

Responsible for querying leo_ads.ad.

This repository deliberately contains persistence logic only.
Business decisions such as targeting and ranking belong in services.

Assumptions:
    - Queries filter by tenant_id to prevent cross-tenant data exposure
    - Status filters prevent serving inactive ads (paused, archived)
    - Ordering by score_weight, then ad_id ensures deterministic ranking
    - Redis should normally cache hot placement->ads paths for performance

Performance notes:
    - The hot path (get_active_by_placement) uses indexed fields only
    - Results should be cached in Redis with TTL=300 seconds
    - Do not increase limit beyond 100; use pagination if needed
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from model.ad import Ad


class AdRepository:
    """
    Repository for ad persistence and queries.

    Responsibilities:
        - Retrieve ads by ID
        - Retrieve active ads for a placement (hot path)
        - Serialize ORM objects to API-safe dicts

    Not responsible for:
        - Targeting, ranking, or business logic
        - Caching or Redis
        - Pydantic validation (for now - should migrate later)
    """

    def __init__(
        self,
        engine: Engine,
    ) -> None:
        self.engine = engine

    # ------------------------------------------------------------------
    # Internal session helper
    # ------------------------------------------------------------------

    def _session(self) -> Session:
        """
        Create a short-lived SQLAlchemy session.

        Later this can be replaced by an injected Database/session factory.
        """

        from sqlalchemy.orm import sessionmaker

        session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )

        return session_factory()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_by_id(
        self,
        ad_id: int,
    ) -> dict[str, Any] | None:
        """
        Retrieve an ad by primary key.
        """

        session = self._session()

        try:
            statement = (
                select(Ad)
                .where(
                    Ad.ad_id == ad_id,
                    Ad.status == "active",
                )
                .limit(1)
            )

            ad = session.execute(statement).scalar_one_or_none()

            if ad is None:
                return None

            return self._to_dict(ad)

        finally:
            session.close()

    def get_active_by_placement(
        self,
        tenant_id: int,
        placement_id: int,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Retrieve active ads belonging to a placement.

        This uses the indexed hot path:

            tenant_id
            placement_id
            status

        For production serving, Redis should normally answer this query first.
        """

        safe_limit = max(1, min(limit, 100))

        session = self._session()

        try:
            statement = (
                select(Ad)
                .where(
                    Ad.tenant_id == tenant_id,
                    Ad.placement_id == placement_id,
                    Ad.status == "active",
                )
                .order_by(
                    Ad.score_weight.desc(),
                    Ad.ad_id.asc(),
                )
                .limit(safe_limit)
            )

            ads = session.execute(statement).scalars().all()

            return [
                self._to_dict(ad)
                for ad in ads
            ]

        finally:
            session.close()

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    @staticmethod
    def _to_dict(
        ad: Ad,
    ) -> dict[str, Any]:
        """
        Convert ORM entity to API-safe dictionary.

        Later this should be replaced with Pydantic response models.
        """

        return {
            "ad_id": ad.ad_id,
            "tenant_id": ad.tenant_id,
            "ad_key": ad.ad_key,
            "campaign_id": ad.campaign_id,
            "creative_id": ad.creative_id,
            "placement_id": ad.placement_id,
            "status": ad.status,
            "score_weight": ad.score_weight,
            "frequency_cap": ad.frequency_cap,
            "metadata": ad.metadata_,
            "created_at": ad.created_at,
            "updated_at": ad.updated_at,
        }

    # ------------------------------------------------------------------
    # Full serving payload (dev/test)
    # ------------------------------------------------------------------
    #
    # Assembles the same JSON shape historically hard-coded in
    # html/ads.data.json (adPlacementId, creative/content, rendering,
    # tracking, advertiser, destination) from real leo_ads rows, so that
    # html/ads-banner.html + html/ads.loader.js can be pointed at this
    # dev API instead of the static fixture.
    #
    # This intentionally issues several small queries per ad (N+1) rather
    # than one large join: it only ever serves a handful of ads for a
    # test page and is not on the production hot path.

    def get_serving_ads(
        self,
        tenant_key: str,
        placement_ref: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Retrieve fully assembled ad payloads for a placement.

        `placement_ref` may be a `placement_key`, a placement's
        `metadata.demoPlacementId` (legacy ids used by ads-banner.html), or
        - as a fallback when no placement matches - an `ad_key`, so a
        specific ad can be requested directly.
        """

        safe_limit = max(1, min(limit, 20))

        session = self._session()

        try:
            placement = session.execute(
                text(
                    """
                    SELECT p.tenant_id, p.placement_id, p.placement_key,
                           p.responsive, p.max_width_px, p.max_height_px,
                           p.metadata
                    FROM leo_ads.placement p
                    JOIN leo_ads.tenant t ON t.tenant_id = p.tenant_id
                    WHERE t.tenant_key = :tenant_key
                      AND p.status = 'active'
                      AND (
                          p.placement_key = :ref
                          OR p.metadata ->> 'demoPlacementId' = :ref
                      )
                    LIMIT 1
                    """
                ),
                {"tenant_key": tenant_key, "ref": placement_ref},
            ).mappings().first()

            if placement is not None:
                ad_filter = "a.placement_id = :placement_id"
                params: dict[str, Any] = {"placement_id": placement["placement_id"]}
            else:
                ad_filter = "a.ad_key = :ad_key"
                params = {"ad_key": placement_ref}

            ads = session.execute(
                text(
                    f"""
                    SELECT a.ad_id, a.ad_key, a.placement_id,
                           cr.creative_id, cr.creative_key, cr.ad_type,
                           cr.format_code, cr.headline, cr.subheadline,
                           cr.body, cr.cta, cr.image_url, cr.content_payload,
                           cr.advertiser_id, c.campaign_key
                    FROM leo_ads.ad a
                    JOIN leo_ads.creative cr ON cr.creative_id = a.creative_id
                    LEFT JOIN leo_ads.campaign c ON c.campaign_id = a.campaign_id
                    JOIN leo_ads.tenant t ON t.tenant_id = a.tenant_id
                    WHERE t.tenant_key = :tenant_key
                      AND a.status = 'active'
                      AND {ad_filter}
                    ORDER BY a.score_weight DESC, a.ad_id ASC
                    LIMIT :limit
                    """
                ),
                {**params, "tenant_key": tenant_key, "limit": safe_limit},
            ).mappings().all()

            if not ads and placement is not None:
                ads = session.execute(
                    text(
                        """
                        SELECT a.ad_id, a.ad_key, a.placement_id,
                               cr.creative_id, cr.creative_key, cr.ad_type,
                               cr.format_code, cr.headline, cr.subheadline,
                               cr.body, cr.cta, cr.image_url, cr.content_payload,
                               cr.advertiser_id, c.campaign_key
                        FROM leo_ads.ad a
                        JOIN leo_ads.creative cr ON cr.creative_id = a.creative_id
                        LEFT JOIN leo_ads.campaign c ON c.campaign_id = a.campaign_id
                        JOIN leo_ads.tenant t ON t.tenant_id = a.tenant_id
                        WHERE t.tenant_key = :tenant_key
                          AND a.status = 'active'
                          AND a.tenant_id = :tenant_id
                        ORDER BY a.score_weight DESC, a.ad_id ASC
                        LIMIT :limit
                        """
                    ),
                    {
                        "tenant_key": tenant_key,
                        "tenant_id": placement["tenant_id"],
                        "limit": safe_limit,
                    },
                ).mappings().all()

            if not ads:
                return []

            if placement is None:
                # Single-ad lookup by ad_key: resolve its own placement so
                # we can still report sensible dimensions/responsiveness.
                placement = session.execute(
                    text(
                        """
                        SELECT placement_id, placement_key, responsive,
                               max_width_px, max_height_px, metadata
                        FROM leo_ads.placement
                        WHERE placement_id = :placement_id
                        """
                    ),
                    {"placement_id": ads[0]["placement_id"]},
                ).mappings().first()

            return [
                self._build_serving_ad(session, placement, ad, placement_ref)
                for ad in ads
            ]

        finally:
            session.close()

    @staticmethod
    def _placement_dimensions(placement: Any) -> dict[str, Any]:
        if placement is None or placement["responsive"]:
            return {"width": 100, "height": 0, "unit": "%", "responsive": True}

        return {
            "width": placement["max_width_px"] or 300,
            "height": placement["max_height_px"] or 250,
            "unit": "px",
        }

    def _build_serving_ad(
        self,
        session: Session,
        placement: Any,
        ad: Any,
        placement_ref: str,
    ) -> dict[str, Any]:
        """
        Fetch the remaining creative-linked rows for a single ad and
        assemble the ads.data.json-shaped payload for it.
        """

        creative_id = ad["creative_id"]
        payload = ad["content_payload"] or {}

        advertiser = None

        if ad["advertiser_id"] is not None:
            advertiser = session.execute(
                text(
                    """
                    SELECT advertiser_key, name, description, title, logo_url
                    FROM leo_ads.advertiser
                    WHERE advertiser_id = :advertiser_id
                    """
                ),
                {"advertiser_id": ad["advertiser_id"]},
            ).mappings().first()

        render = session.execute(
            text(
                """
                SELECT render_type_code, template_key, loader_src,
                       loader_async, container_id, container_class_name,
                       render_config
                FROM leo_ads.creative_render
                WHERE creative_id = :creative_id
                LIMIT 1
                """
            ),
            {"creative_id": creative_id},
        ).mappings().first()

        destination = session.execute(
            text(
                """
                SELECT destination_type_code, url, final_url
                FROM leo_ads.destination
                WHERE creative_id = :creative_id
                LIMIT 1
                """
            ),
            {"creative_id": creative_id},
        ).mappings().first()

        tracking_rows = session.execute(
            text(
                """
                SELECT event_type, endpoint_url
                FROM leo_ads.tracking_endpoint
                WHERE creative_id = :creative_id
                """
            ),
            {"creative_id": creative_id},
        ).mappings().all()

        tracking = {
            f"{row['event_type']}Url": row["endpoint_url"] for row in tracking_rows
        }

        advertiser_dict = (
            {
                "id": advertiser["advertiser_key"],
                "name": advertiser["name"],
                "description": advertiser["description"],
                "title": advertiser["title"],
                "logoUrl": advertiser["logo_url"],
            }
            if advertiser is not None
            else None
        )

        rendering: dict[str, Any] = {
            "type": render["render_type_code"] if render else "native_json",
            "template": render["template_key"] if render else None,
        }

        if render is not None and render["render_type_code"] == "js_tag":
            rendering["loader"] = {
                "src": render["loader_src"],
                "async": bool(render["loader_async"]),
            }
            rendering["config"] = render["render_config"] or {}
            rendering["container"] = {
                "id": render["container_id"],
                "className": render["container_class_name"],
            }

        result: dict[str, Any] = {
            "adId": ad["ad_key"],
            "adType": ad["ad_type"],
            "adFormat": ad["format_code"],
            # Always echo back the requested ref (not the placement's own
            # demoPlacementId) so ads.loader.js's selectAdForPlacement, which
            # matches on the placementId it queried with, always finds a hit.
            "adPlacementId": placement_ref,
            "placement": self._placement_dimensions(placement),
            "source": {
                "type": payload.get("provider", "local"),
                "provider": payload.get("provider", "internal"),
                "campaignId": ad["campaign_key"],
                "creativeId": ad["creative_key"],
            },
            "rendering": rendering,
            "tracking": tracking,
            "advertiser": advertiser_dict,
            "destination": (
                {
                    "type": destination["destination_type_code"],
                    "url": destination["url"],
                    "finalUrl": destination["final_url"],
                }
                if destination is not None
                else None
            ),
        }

        if ad["format_code"] == "product_carousel":
            result["content"] = {
                "headline": ad["headline"],
                "label": payload.get("label"),
            }
            result["adItems"] = self._get_creative_items(session, creative_id)
        elif ad["format_code"] in ("native", "native_product"):
            result["content"] = {
                "id": payload.get("contentId") or payload.get("offerId"),
                "label": payload.get("label", "Sponsored"),
                "headline": ad["headline"],
                "body": ad["body"],
                "cta": ad["cta"],
                "imageUrl": ad["image_url"],
            }
        else:
            result["creative"] = {
                "id": ad["creative_key"],
                "headline": ad["headline"],
                "subheadline": ad["subheadline"],
                "cta": ad["cta"],
                "imageUrl": ad["image_url"],
                "badge": payload.get("badge"),
            }

        return result

    @staticmethod
    def _get_creative_items(session: Session, creative_id: int) -> list[dict[str, Any]]:
        item_rows = session.execute(
            text(
                """
                SELECT external_item_id, item_name, price_amount,
                       discount_text, image_url, destination_url,
                       highlight_text
                FROM leo_ads.creative_item
                WHERE creative_id = :creative_id
                ORDER BY sort_order ASC
                """
            ),
            {"creative_id": creative_id},
        ).mappings().all()

        return [
            {
                "id": row["external_item_id"],
                "name": row["item_name"],
                "price": (
                    f"{int(row['price_amount']):,}đ".replace(",", ".")
                    if row["price_amount"] is not None
                    else None
                ),
                "discount": row["discount_text"],
                "imageUrl": row["image_url"],
                "destination": {"url": row["destination_url"]},
                "highlightText": row["highlight_text"],
            }
            for row in item_rows
        ]