"""Tenant-scoped persistence for promotions campaign and creative management."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from model.campaign import Campaign
from model.creative import Creative
from model.management_schemas import (
    CampaignInput, CreativeInput, Customer360CampaignInput, Customer360CreativeInput,
)

Entity = Campaign | Creative
Payload = CampaignInput | CreativeInput

_REFERENCES = {
    "campaign": ("campaign", "campaign_id"),
    "advertiser": ("advertiser", "advertiser_id"),
    "source_account": ("source_account", "source_account_id"),
    "source_asset": ("source_asset", "source_asset_id"),
}


class InvalidReference(ValueError):
    """A referenced record is not available in the authenticated tenant."""


class ManagementConflict(ValueError):
    """A management write conflicts with existing data."""


class ManagementRepository:
    """Persist management records while enforcing tenant ownership of links."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    @staticmethod
    def serialize(entity: Entity) -> dict[str, Any]:
        """Return the public fields of a campaign or creative."""
        values = {
            column.key: getattr(entity, "metadata_" if column.key == "metadata" else column.key)
            for column in entity.__table__.columns
        }
        return values

    @staticmethod
    def _validate_references(session: Session, tenant_id: int, payload: Payload) -> None:
        references = {
            "advertiser": payload.advertiser_id,
            "source_account": payload.source_account_id if isinstance(payload, CampaignInput) else None,
            "campaign": payload.campaign_id if isinstance(payload, CreativeInput) else None,
            "source_asset": payload.source_asset_id if isinstance(payload, CreativeInput) else None,
        }
        for name, reference_id in references.items():
            if reference_id is None:
                continue
            table, column = _REFERENCES[name]
            row = session.execute(
                text(
                    f"SELECT {column} FROM leo_ads.{table} "
                    f"WHERE tenant_id = :tenant_id AND {column} = :reference_id FOR KEY SHARE"
                ),
                {"tenant_id": tenant_id, "reference_id": reference_id},
            ).first()
            if row is None:
                raise InvalidReference(f"Referenced {name} is unavailable")
        if isinstance(payload, CreativeInput) and payload.render_type_code:
            row = session.execute(
                text("SELECT render_type_code FROM leo_ads.render_type WHERE render_type_code = :code"),
                {"code": payload.render_type_code},
            ).first()
            if row is None:
                raise InvalidReference("Referenced render type is unavailable")

    @staticmethod
    def _apply(entity: Entity, payload: Payload) -> None:
        for key, value in payload.model_dump().items():
            setattr(entity, "metadata_" if key == "metadata" else key, value)

    def list(self, model: type[Campaign] | type[Creative], tenant_id: int, offset: int, limit: int) -> list[dict[str, Any]]:
        """List a bounded page of records belonging to one tenant."""
        id_column = Campaign.campaign_id if model is Campaign else Creative.creative_id
        with Session(self.engine) as session:
            items = session.scalars(
                select(model)
                .where(model.tenant_id == tenant_id)
                .order_by(id_column)
                .offset(offset)
                .limit(limit)
            ).all()
            return [self.serialize(item) for item in items]

    def get(self, model: type[Campaign] | type[Creative], tenant_id: int, item_id: int) -> dict[str, Any] | None:
        """Return one tenant-owned record, or None when it is unavailable."""
        id_column = Campaign.campaign_id if model is Campaign else Creative.creative_id
        with Session(self.engine) as session:
            item = session.scalar(select(model).where(model.tenant_id == tenant_id, id_column == item_id))
            return self.serialize(item) if item else None

    def save(
        self,
        model: type[Campaign] | type[Creative],
        tenant_id: int,
        payload: Payload,
        item_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Create or replace a tenant-owned record atomically."""
        id_column = Campaign.campaign_id if model is Campaign else Creative.creative_id
        try:
            with Session(self.engine) as session, session.begin():
                if item_id is None:
                    if (payload.campaign_key if isinstance(payload, CampaignInput) else payload.creative_key).startswith("customer360:"):
                        raise ManagementConflict("Customer 360 keys are reserved for synchronization")
                    item = model(tenant_id=tenant_id)
                    session.add(item)
                else:
                    item = session.scalar(
                        select(model)
                        .where(model.tenant_id == tenant_id, id_column == item_id)
                        .with_for_update()
                    )
                    if item is None:
                        return None
                    old_key = item.campaign_key if isinstance(item, Campaign) else item.creative_key
                    new_key = payload.campaign_key if isinstance(payload, CampaignInput) else payload.creative_key
                    if (old_key.startswith("customer360:") or new_key.startswith("customer360:")) and (
                        old_key != new_key or (
                            isinstance(item, Creative) and isinstance(payload, CreativeInput)
                            and item.version_no != payload.version_no
                        )
                    ):
                        raise ManagementConflict("Customer 360 synchronization identity cannot be changed")
                self._validate_references(session, tenant_id, payload)
                self._apply(item, payload)
                if item_id is not None:
                    item.updated_at = func.now()
                session.flush()
                session.refresh(item)
                return self.serialize(item)
        except IntegrityError as exc:
            raise ManagementConflict("Record conflicts with existing or referenced data") from exc

    def delete(self, model: type[Campaign] | type[Creative], tenant_id: int, item_id: int) -> bool:
        """Delete a record only when it is not referenced by ads or creatives."""
        id_column = Campaign.campaign_id if model is Campaign else Creative.creative_id
        try:
            with Session(self.engine) as session, session.begin():
                item = session.scalar(
                    select(model)
                    .where(model.tenant_id == tenant_id, id_column == item_id)
                    .with_for_update()
                )
                if item is None:
                    return False
                key = item.campaign_key if isinstance(item, Campaign) else item.creative_key
                if key.startswith("customer360:"):
                    raise ManagementConflict("Customer 360 synchronized records cannot be deleted here")
                tables = ("ad", "creative") if model is Campaign else ("ad",)
                for table in tables:
                    if session.execute(
                        text(f"SELECT 1 FROM leo_ads.{table} WHERE {id_column.key} = :item_id LIMIT 1"),
                        {"item_id": item_id},
                    ).first():
                        raise ManagementConflict("Record is referenced and cannot be deleted")
                session.delete(item)
                session.flush()
                return True
        except IntegrityError as exc:
            raise ManagementConflict("Record is referenced and cannot be deleted") from exc

    def sync_customer360(
        self,
        tenant_id: int,
        source_tenant_id: uuid.UUID,
        source_id: uuid.UUID,
        payload: Customer360CampaignInput | Customer360CreativeInput,
    ) -> dict[str, Any]:
        """Atomically synchronize one source entity into its unique draft record."""
        key = f"customer360:{source_id}"
        is_campaign = isinstance(payload, Customer360CampaignInput)
        try:
            with Session(self.engine) as session, session.begin():
                mapped = session.execute(
                    text(
                        "SELECT 1 FROM leo_ads.customer360_tenant_map "
                        "WHERE customer360_tenant_id = :source AND tenant_id = :tenant FOR KEY SHARE"
                    ),
                    {"source": source_tenant_id, "tenant": tenant_id},
                ).first()
                if mapped is None:
                    raise InvalidReference("Customer 360 tenant mapping is unavailable")
                if is_campaign:
                    values = payload.model_dump()
                    statement = text("""
                        INSERT INTO leo_ads.campaign
                          (tenant_id, campaign_key, name, objective, budget_amount, currency, status, metadata)
                        VALUES (:tenant, :key, :name, :objective, :budget_amount, :currency, 'draft', CAST(:metadata AS jsonb))
                        ON CONFLICT (tenant_id, campaign_key) DO UPDATE SET
                          name = EXCLUDED.name, objective = EXCLUDED.objective,
                          budget_amount = EXCLUDED.budget_amount, currency = EXCLUDED.currency,
                          metadata = leo_ads.campaign.metadata || EXCLUDED.metadata,
                          updated_at = now()
                        WHERE leo_ads.campaign.status <> 'active'
                        RETURNING campaign_id
                    """)
                    values["metadata"] = json.dumps(values["metadata"], allow_nan=False)
                    model = Campaign
                else:
                    values = payload.model_dump()
                    statement = text("""
                        INSERT INTO leo_ads.creative
                          (tenant_id, creative_key, version_no, ad_type, format_code, status,
                           headline, body, image_url, cta, content_payload)
                        VALUES (:tenant, :key, 1, :ad_type, :format_code, 'draft',
                                :headline, :body, :image_url, :cta, CAST(:content_payload AS jsonb))
                        ON CONFLICT (tenant_id, creative_key, version_no) DO UPDATE SET
                          ad_type = EXCLUDED.ad_type, format_code = EXCLUDED.format_code,
                          headline = EXCLUDED.headline, body = EXCLUDED.body,
                          image_url = EXCLUDED.image_url, cta = EXCLUDED.cta,
                          content_payload = leo_ads.creative.content_payload || EXCLUDED.content_payload,
                          updated_at = now()
                        WHERE leo_ads.creative.status <> 'active'
                        RETURNING creative_id
                    """)
                    values["content_payload"] = json.dumps(values["content_payload"], allow_nan=False)
                    model = Creative
                item_id = session.execute(statement, {**values, "tenant": tenant_id, "key": key}).scalar_one_or_none()
                if item_id is None:
                    raise ManagementConflict("Pause the active promotion before synchronizing changes")
                item = session.get(model, item_id)
                if item is None:
                    raise ManagementConflict("Synchronized record could not be read")
                return self.serialize(item)
        except IntegrityError as exc:
            raise ManagementConflict("Record conflicts with existing or referenced data") from exc
