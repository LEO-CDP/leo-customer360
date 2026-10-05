"""Tenant-scoped persistence for product items and linked content records."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from leo_customer360_dao.config import settings
from leo_customer360_dao.models.content import CdpContentItem
from leo_customer360_dao.schemas.product_item import ProductItemCreate

_PRODUCT_ITEM_SELECT = f"""
    SELECT
        product.product_item_id,
        product.tenant_id,
        product.content_item_id,
        product.domain,
        product.product_type,
        product.source_id,
        product.source_type,
        product.product_id_type,
        product.product_id,
        product.keywords,
        product.ext_attributes,
        product.original_price,
        product.sale_price,
        product.currency,
        COALESCE(content.title, product.source_fields->>'Name') AS title,
        COALESCE(content.summary, product.source_fields->>'Description') AS summary,
        COALESCE(content.image_url, product.source_fields->>'Image_URL') AS image_url,
        COALESCE(content.cta_label, product.source_fields->>'CTA_Label') AS cta_label,
        COALESCE(content.cta_url, product.source_fields->>'Full_URL') AS cta_url,
        COALESCE(content.status_code, 1) AS status_code,
        content.created_at,
        content.updated_at
    FROM {settings.db_schema}.cdp_product_items AS product
    LEFT JOIN {settings.db_schema}.cdp_content_items AS content
      ON content.tenant_id = product.tenant_id
     AND content.content_item_id = product.content_item_id
"""


class ProductItemRepository:
    """Create and list product records with their Customer 360 content item."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_products(
        self,
        *,
        tenant_id: uuid.UUID,
        skip: int,
        limit: int,
        domain: str | None = None,
        q: str | None = None,
    ) -> list[dict[str, Any]]:
        """List products for one tenant, optionally filtered by domain or text."""
        statement = text(
            _PRODUCT_ITEM_SELECT
            + """
            WHERE product.tenant_id = :tenant_id
              AND (:domain IS NULL OR product.domain = :domain)
              AND (
                    :search IS NULL
                    OR content.title ILIKE :search
                    OR COALESCE(content.summary, '') ILIKE :search
                    OR product.source_id ILIKE :search
                    OR product.source_type ILIKE :search
                    OR product.product_id ILIKE :search
                    OR product.product_id_type ILIKE :search
                  )
            ORDER BY COALESCE(content.updated_at, product.updated_at) DESC NULLS LAST,
                product.product_item_id
            LIMIT :limit OFFSET :skip
            """
        )
        search = f"%{q.strip()}%" if q and q.strip() else None
        rows = self.session.execute(
            statement,
            {
                "tenant_id": tenant_id,
                "domain": domain,
                "search": search,
                "limit": limit,
                "skip": skip,
            },
        ).mappings().all()
        return [dict(row) for row in rows]

    def create_product(
        self,
        *,
        tenant_id: uuid.UUID,
        payload: ProductItemCreate,
    ) -> dict[str, Any]:
        """Atomically create a content item and its source product metadata."""
        content_item_id = uuid.uuid4()
        product_item_id = uuid.uuid4().hex
        content_item = CdpContentItem(
            content_item_id=content_item_id,
            tenant_id=tenant_id,
            domain=payload.domain,
            item_type="product",
            title=payload.title,
            summary=payload.summary,
            image_url=payload.image_url,
            cta_label=payload.cta_label,
            cta_url=payload.cta_url,
            segment_tags=payload.keywords,
            status_code=1,
        )
        try:
            self.session.add(content_item)
            self.session.flush()
            self.session.execute(
                text(
                    f"""
                    INSERT INTO {settings.db_schema}.cdp_product_items (
                        product_item_id, tenant_id, content_item_id, domain,
                        product_type, source_id, source_type, product_id_type,
                        product_id, keywords, ext_attributes, original_price,
                        sale_price, currency, source_fields
                    ) VALUES (
                        :product_item_id, :tenant_id, :content_item_id, :domain,
                        :product_type, :source_id, :source_type, :product_id_type,
                        :product_id, :keywords, CAST(:ext_attributes AS jsonb),
                        :original_price, :sale_price, :currency,
                        CAST(:source_fields AS jsonb)
                    )
                    """
                ),
                {
                    "product_item_id": product_item_id,
                    "tenant_id": tenant_id,
                    "content_item_id": content_item_id,
                    "domain": payload.domain,
                    "product_type": payload.product_type,
                    "source_id": payload.source_id,
                    "source_type": payload.source_type,
                    "product_id_type": payload.product_id_type,
                    "product_id": payload.product_id,
                    "keywords": payload.keywords,
                    "ext_attributes": json.dumps(payload.ext_attributes, allow_nan=False),
                    "original_price": payload.original_price,
                    "sale_price": payload.sale_price,
                    "currency": payload.currency,
                    "source_fields": json.dumps(
                        payload.model_dump(mode="json"),
                        ensure_ascii=False,
                        allow_nan=False,
                    ),
                },
            )
            result = self.get_product(
                tenant_id=tenant_id,
                product_item_id=product_item_id,
            )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return result

    def get_product(
        self,
        *,
        tenant_id: uuid.UUID,
        product_item_id: str,
    ) -> dict[str, Any]:
        """Read one product and its content fields within the owning tenant."""
        row = self.session.execute(
            text(
                _PRODUCT_ITEM_SELECT
                + """
                WHERE product.tenant_id = :tenant_id
                  AND product.product_item_id = :product_item_id
                """
            ),
            {"tenant_id": tenant_id, "product_item_id": product_item_id},
        ).mappings().first()
        if row is None:
            raise LookupError(f"Product item '{product_item_id}' was not found")
        return dict(row)
