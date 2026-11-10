"""Explicit tenant-scoped conversion of Customer 360 records into promotions."""

from __future__ import annotations

import json
import uuid
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from core.repositories.campaign_draft_repository import CampaignDraftNotFoundError, CampaignDraftRepository
from core.repositories.content_repository import ContentRepository
from core.repositories.product_item_repository import ProductItemRepository
from core.services.promotion_config import promotion_settings


class PromotionService:
    """Convert tenant-owned source records through the promotions HTTP API."""

    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _connection(tenant_id: uuid.UUID) -> tuple[str, str]:
        try:
            tokens = json.loads(promotion_settings.promotion_management_tokens.get_secret_value())
            tenant_keys = json.loads(promotion_settings.promotion_tenant_keys)
            if not isinstance(tokens, dict) or not tokens or not isinstance(tenant_keys, dict) or not tenant_keys:
                raise ValueError("empty token mapping")
            normalized = {str(uuid.UUID(key)): value for key, value in tenant_keys.items()}
            if (
                len(normalized) != len(tenant_keys)
                or any(not isinstance(key, str) or not key or key != key.strip() for key in normalized.values())
                or len(set(normalized.values())) != len(normalized)
                or any(not isinstance(key, str) or not key or key != key.strip() or not isinstance(token, str) or len(token) < 32 or not token.isascii() or any(c.isspace() for c in token) for key, token in tokens.items())
                or len(set(tokens.values())) != len(tokens)
            ):
                raise ValueError("invalid token mapping")
            token = tokens.get(normalized.get(str(tenant_id)))
            if not token:
                raise ValueError("tenant token not configured")
            url = promotion_settings.promotion_api_url.rstrip("/")
            parsed = urlsplit(url)
            if (
                parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"} and not promotion_settings.promotion_allow_insecure_http)
            ):
                raise ValueError("invalid promotions API URL")
            return url, token
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(status_code=503, detail="Promotions integration is not configured for this tenant") from None

    def _sync(self, tenant_id: uuid.UUID, resource: str, source_id: uuid.UUID, payload: dict) -> dict:
        url, token = self._connection(tenant_id)
        try:
            with httpx.Client(timeout=10.0, follow_redirects=False) as client:
                response = client.put(
                    f"{url}/management/customer360/{tenant_id}/{resource}/{source_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail="Promotions service is unavailable") from exc
        if response.status_code == 409:
            raise HTTPException(status_code=409, detail="Promotion could not be synchronized; check its current status")
        if response.status_code == 422:
            raise HTTPException(status_code=422, detail="Source fields do not meet promotions validation")
        if response.status_code in {401, 403, 503}:
            raise HTTPException(status_code=503, detail="Promotions authentication or tenant mapping is unavailable")
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail="Promotions service returned an unexpected response")
        try:
            result = response.json()
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="Promotions service returned an invalid response") from exc
        id_field = "campaign_id" if resource == "campaigns" else "creative_id"
        key_field = "campaign_key" if resource == "campaigns" else "creative_key"
        if (
            not isinstance(result, dict)
            or not isinstance(result.get(id_field), int)
            or isinstance(result.get(id_field), bool)
            or result[id_field] <= 0
            or result.get(key_field) != f"customer360:{source_id}"
            or (resource == "content" and result.get("version_no") != 1)
        ):
            raise HTTPException(status_code=502, detail="Promotions service returned an invalid response")
        return result

    def campaign(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> dict:
        """Convert or refresh one tenant-owned campaign."""
        try:
            campaign = CampaignDraftRepository(self.session).get_campaign(tenant_id, campaign_id)
        except CampaignDraftNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Campaign not found") from exc
        return self._sync(tenant_id, "campaigns", campaign_id, {
            "name": campaign.name,
            "objective": campaign.objective,
            "budget_amount": str(campaign.budget_amount) if campaign.budget_amount is not None else None,
            "currency": campaign.currency,
            "metadata": {
                "customer360_tenant_id": str(tenant_id),
                "customer360_campaign_id": str(campaign_id),
                "customer360_campaign_code": campaign.campaign_code,
                "customer360_start_date": campaign.start_date.isoformat() if campaign.start_date else None,
                "customer360_end_date": campaign.end_date.isoformat() if campaign.end_date else None,
            },
        })

    def content(self, tenant_id: uuid.UUID, content_id: uuid.UUID, ad_type: str, format_code: str) -> dict:
        """Convert or refresh the creative shared by one content item and its product."""
        content = ContentRepository(self.session).get_item(content_id, tenant_id)
        if content is None:
            raise HTTPException(status_code=404, detail="Content item not found")
        return self._sync(tenant_id, "content", content_id, {
            "ad_type": ad_type,
            "format_code": format_code,
            "headline": content.title,
            "body": content.summary,
            "image_url": content.image_url,
            "cta": content.cta_label,
            "content_payload": {
                "customer360_tenant_id": str(tenant_id),
                "customer360_content_item_id": str(content_id),
                "customer360_item_type": content.item_type,
                "customer360_domain": content.domain,
                "customer360_segment_tags": content.segment_tags,
                "customer360_cta_url": content.cta_url,
            },
        })

    def product(self, tenant_id: uuid.UUID, product_id: str, ad_type: str, format_code: str) -> dict:
        """Convert a product using its linked content identity."""
        try:
            product = ProductItemRepository(self.session).get_product(tenant_id=tenant_id, product_item_id=product_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Product not found") from exc
        content_id = product.get("content_item_id")
        if content_id is None:
            raise HTTPException(status_code=409, detail="Product has no linked content item")
        return self.content(tenant_id, uuid.UUID(str(content_id)), ad_type, format_code)
