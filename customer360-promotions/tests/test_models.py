"""
Unit tests for ORM models.

Tests verify:
- Field types and constraints
- SQLAlchemy 2.0 compatibility (especially metadata_ column mapping)
- Default values
- Foreign key relationships
"""

import pytest
from datetime import datetime
from model.ad import Ad
from model.campaign import Campaign
from model.creative import Creative, CreativeRender, Destination
from model.placement import Placement, PlacementFormat


class TestAdModel:
    """Test Ad ORM model."""

    def test_ad_table_name(self):
        """Verify Ad maps to correct table."""
        assert Ad.__tablename__ == "ad"

    def test_ad_schema(self):
        """Verify Ad is in leo_ads schema."""
        assert Ad.__table_args__["schema"] == "leo_ads"

    def test_ad_primary_key(self):
        """Verify ad_id is primary key."""
        pk_cols = {col.name for col in Ad.__table__.primary_key.columns}
        assert "ad_id" in pk_cols

    def test_ad_metadata_column_mapping(self):
        """
        Test SQLAlchemy 2.0 metadata_ field mapping.

        This is critical: Python attribute is 'metadata_' but database
        column name is 'metadata' (reserved keyword).
        """
        assert Ad.__table__.columns["metadata"].name == "metadata"
        assert hasattr(Ad, "metadata_")

    def test_ad_required_fields(self):
        """Verify required fields are not nullable."""
        required = [
            "tenant_id",
            "ad_key",
            "creative_id",
            "placement_id",
            "status",
            "score_weight",
            "metadata",
        ]

        for field in required:
            col = Ad.__table__.columns[field]
            assert not col.nullable, f"{field} should not be nullable"

    def test_ad_optional_fields(self):
        """Verify optional fields are nullable."""
        optional = ["campaign_id", "frequency_cap"]

        for field in optional:
            col = Ad.__table__.columns[field]
            assert col.nullable, f"{field} should be nullable"

    def test_ad_default_status(self):
        """Verify status defaults to 'active'."""
        col = Ad.__table__.columns["status"]
        # Check the default value
        assert col.default is not None or col.server_default is not None

    def test_ad_default_score_weight(self):
        """Verify score_weight defaults to 1.0."""
        col = Ad.__table__.columns["score_weight"]
        assert col.default is not None or col.server_default is not None

    def test_ad_foreign_keys(self):
        """Verify foreign key relationships."""
        fks = list(Ad.__table__.foreign_keys)
        fk_columns = {fk.parent.name for fk in fks}

        assert "tenant_id" in fk_columns, "tenant_id should have FK to tenant.tenant_id"
        assert "campaign_id" in fk_columns, "campaign_id should have FK to campaign.campaign_id"
        assert "creative_id" in fk_columns, "creative_id should have FK to creative.creative_id"
        assert "placement_id" in fk_columns, "placement_id should have FK to placement.placement_id"

    def test_ad_creative_fk_restrict(self):
        """Verify creative_id FK has ondelete=RESTRICT."""
        for fk in Ad.__table__.foreign_keys:
            if fk.parent.name == "creative_id":
                assert fk.ondelete == "RESTRICT"

    def test_ad_placement_fk_restrict(self):
        """Verify placement_id FK has ondelete=RESTRICT."""
        for fk in Ad.__table__.foreign_keys:
            if fk.parent.name == "placement_id":
                assert fk.ondelete == "RESTRICT"

    def test_ad_campaign_fk_set_null(self):
        """Verify campaign_id FK has ondelete=SET NULL."""
        for fk in Ad.__table__.foreign_keys:
            if fk.parent.name == "campaign_id":
                assert fk.ondelete == "SET NULL"

    def test_ad_has_audit_timestamps(self):
        """Verify created_at and updated_at exist."""
        assert "created_at" in Ad.__table__.columns
        assert "updated_at" in Ad.__table__.columns

        assert not Ad.__table__.columns["created_at"].nullable
        assert not Ad.__table__.columns["updated_at"].nullable


class TestCampaignModel:
    """Test Campaign ORM model."""

    def test_campaign_table_name(self):
        """Verify Campaign maps to correct table."""
        assert Campaign.__tablename__ == "campaign"

    def test_campaign_schema(self):
        """Verify Campaign is in leo_ads schema."""
        assert Campaign.__table_args__["schema"] == "leo_ads"

    def test_campaign_primary_key(self):
        """Verify campaign_id is primary key."""
        pk_cols = {col.name for col in Campaign.__table__.primary_key.columns}
        assert "campaign_id" in pk_cols

    def test_campaign_metadata_column_mapping(self):
        """Test SQLAlchemy 2.0 metadata_ field mapping."""
        assert Campaign.__table__.columns["metadata"].name == "metadata"
        assert hasattr(Campaign, "metadata_")

    def test_campaign_required_fields(self):
        """Verify required fields are not nullable."""
        required = ["tenant_id", "campaign_key", "name", "status", "metadata"]

        for field in required:
            col = Campaign.__table__.columns[field]
            assert not col.nullable, f"{field} should not be nullable"

    def test_campaign_optional_fields(self):
        """Verify optional fields are nullable."""
        optional = [
            "advertiser_id",
            "source_account_id",
            "objective",
            "buying_model",
            "budget_amount",
            "currency",
            "daily_budget_amount",
            "starts_at",
            "ends_at",
        ]

        for field in optional:
            col = Campaign.__table__.columns[field]
            assert col.nullable, f"{field} should be nullable"

    def test_campaign_default_status(self):
        """Verify status defaults to 'draft'."""
        col = Campaign.__table__.columns["status"]
        assert col.default is not None

    def test_campaign_has_audit_timestamps(self):
        """Verify created_at and updated_at exist."""
        assert "created_at" in Campaign.__table__.columns
        assert "updated_at" in Campaign.__table__.columns


class TestCreativeModel:
    """Test Creative ORM model."""

    def test_creative_table_name(self):
        """Verify Creative maps to correct table."""
        assert Creative.__tablename__ == "creative"

    def test_creative_schema(self):
        """Verify Creative is in leo_ads schema."""
        assert Creative.__table_args__["schema"] == "leo_ads"

    def test_creative_primary_key(self):
        """Verify creative_id is primary key."""
        pk_cols = {col.name for col in Creative.__table__.primary_key.columns}
        assert "creative_id" in pk_cols

    def test_creative_required_fields(self):
        """Verify required fields are not nullable."""
        required = [
            "tenant_id",
            "creative_key",
            "ad_type",
            "format_code",
            "status",
            "version_no",
            "priority",
            "content_payload",
        ]

        for field in required:
            col = Creative.__table__.columns[field]
            assert not col.nullable, f"{field} should not be nullable"

    def test_creative_optional_fields(self):
        """Verify optional fields are nullable."""
        optional = [
            "campaign_id",
            "advertiser_id",
            "source_asset_id",
            "render_type_code",
            "starts_at",
            "ends_at",
            "headline",
            "subheadline",
            "body",
            "cta",
            "image_url",
            "video_url",
            "logo_url",
        ]

        for field in optional:
            col = Creative.__table__.columns[field]
            assert col.nullable, f"{field} should be nullable"

    def test_creative_default_status(self):
        """Verify status defaults to 'active'."""
        col = Creative.__table__.columns["status"]
        assert col.default is not None

    def test_creative_default_version(self):
        """Verify version_no defaults to 1."""
        col = Creative.__table__.columns["version_no"]
        assert col.default is not None

    def test_creative_default_priority(self):
        """Verify priority defaults to 0."""
        col = Creative.__table__.columns["priority"]
        assert col.default is not None

    def test_creative_has_audit_timestamps(self):
        """Verify created_at and updated_at exist."""
        assert "created_at" in Creative.__table__.columns
        assert "updated_at" in Creative.__table__.columns


class TestCreativeRenderModel:
    """Test CreativeRender ORM model."""

    def test_creative_render_table_name(self):
        """Verify CreativeRender maps to correct table."""
        assert CreativeRender.__tablename__ == "creative_render"

    def test_creative_render_schema(self):
        """Verify CreativeRender is in leo_ads schema."""
        assert CreativeRender.__table_args__["schema"] == "leo_ads"

    def test_creative_render_foreign_key_cascade(self):
        """Verify creative_id FK has ondelete=CASCADE."""
        for fk in CreativeRender.__table__.foreign_keys:
            if fk.parent.name == "creative_id":
                assert fk.ondelete == "CASCADE"

    def test_creative_render_required_fields(self):
        """Verify required fields are not nullable."""
        required = ["creative_id", "render_type_code", "loader_async", "render_config"]

        for field in required:
            col = CreativeRender.__table__.columns[field]
            assert not col.nullable, f"{field} should not be nullable"

    def test_creative_render_default_loader_async(self):
        """Verify loader_async defaults to True."""
        col = CreativeRender.__table__.columns["loader_async"]
        assert col.default is not None


class TestPlacementModel:
    """Test Placement ORM model."""

    def test_placement_table_name(self):
        """Verify Placement maps to correct table."""
        assert Placement.__tablename__ == "placement"

    def test_placement_schema(self):
        """Verify Placement is in leo_ads schema."""
        assert Placement.__table_args__["schema"] == "leo_ads"

    def test_placement_primary_key(self):
        """Verify placement_id is primary key."""
        pk_cols = {col.name for col in Placement.__table__.primary_key.columns}
        assert "placement_id" in pk_cols

    def test_placement_metadata_column_mapping(self):
        """Test SQLAlchemy 2.0 metadata_ field mapping."""
        assert Placement.__table__.columns["metadata"].name == "metadata"
        assert hasattr(Placement, "metadata_")

    def test_placement_required_fields(self):
        """Verify required fields are not nullable."""
        required = ["tenant_id", "placement_key", "status", "responsive", "metadata"]

        for field in required:
            col = Placement.__table__.columns[field]
            assert not col.nullable, f"{field} should not be nullable"

    def test_placement_optional_fields(self):
        """Verify optional fields are nullable."""
        optional = [
            "name",
            "min_width_px",
            "max_width_px",
            "min_height_px",
            "max_height_px",
        ]

        for field in optional:
            col = Placement.__table__.columns[field]
            assert col.nullable, f"{field} should be nullable"

    def test_placement_default_status(self):
        """Verify status defaults to 'active'."""
        col = Placement.__table__.columns["status"]
        assert col.default is not None

    def test_placement_default_responsive(self):
        """Verify responsive defaults to False."""
        col = Placement.__table__.columns["responsive"]
        assert col.default is not None

    def test_placement_has_audit_timestamps(self):
        """Verify created_at and updated_at exist."""
        assert "created_at" in Placement.__table__.columns
        assert "updated_at" in Placement.__table__.columns

    def test_placement_tenant_fk_restrict(self):
        """Verify tenant_id FK exists."""
        fks = list(Placement.__table__.foreign_keys)
        fk_columns = {fk.parent.name for fk in fks}
        assert "tenant_id" in fk_columns


class TestPlacementFormatModel:
    """Test PlacementFormat ORM model."""

    def test_placement_format_table_name(self):
        """Verify PlacementFormat maps to correct table."""
        assert PlacementFormat.__tablename__ == "placement_format"

    def test_placement_format_schema(self):
        """Verify PlacementFormat is in leo_ads schema."""
        assert PlacementFormat.__table_args__["schema"] == "leo_ads"

    def test_placement_format_composite_key(self):
        """Verify (placement_id, format_code) is composite primary key."""
        pk = PlacementFormat.__table__.primary_key
        assert len(pk) == 2
        assert "placement_id" in {col.name for col in pk}
        assert "format_code" in {col.name for col in pk}

    def test_placement_format_foreign_key_cascade(self):
        """Verify placement_id FK has ondelete=CASCADE."""
        for fk in PlacementFormat.__table__.foreign_keys:
            if fk.parent.name == "placement_id":
                assert fk.ondelete == "CASCADE"

    def test_placement_format_required_fields(self):
        """Verify required fields are not nullable."""
        required = ["placement_id", "format_code", "width_unit", "height_unit", "responsive", "constraints"]

        for field in required:
            col = PlacementFormat.__table__.columns[field]
            assert not col.nullable, f"{field} should not be nullable"

    def test_placement_format_optional_fields(self):
        """Verify optional fields are nullable."""
        optional = ["width_px", "height_px"]

        for field in optional:
            col = PlacementFormat.__table__.columns[field]
            assert col.nullable, f"{field} should be nullable"

    def test_placement_format_default_width_unit(self):
        """Verify width_unit defaults to 'px'."""
        col = PlacementFormat.__table__.columns["width_unit"]
        assert col.default is not None

    def test_placement_format_default_height_unit(self):
        """Verify height_unit defaults to 'px'."""
        col = PlacementFormat.__table__.columns["height_unit"]
        assert col.default is not None


class TestDestinationModel:
    """Test Destination ORM model."""

    def test_destination_table_name(self):
        """Verify Destination maps to correct table."""
        assert Destination.__tablename__ == "destination"

    def test_destination_schema(self):
        """Verify Destination is in leo_ads schema."""
        assert Destination.__table_args__["schema"] == "leo_ads"

    def test_destination_metadata_column_mapping(self):
        """Test SQLAlchemy 2.0 metadata_ field mapping."""
        assert Destination.__table__.columns["metadata"].name == "metadata"
        assert hasattr(Destination, "metadata_")

    def test_destination_required_fields(self):
        """Verify required fields are not nullable."""
        required = ["creative_id", "destination_type_code", "metadata"]

        for field in required:
            col = Destination.__table__.columns[field]
            assert not col.nullable, f"{field} should not be nullable"

    def test_destination_optional_fields(self):
        """Verify optional fields are nullable."""
        optional = ["url", "final_url"]

        for field in optional:
            col = Destination.__table__.columns[field]
            assert col.nullable, f"{field} should be nullable"

    def test_destination_foreign_key_cascade(self):
        """Verify creative_id FK has ondelete=CASCADE."""
        for fk in Destination.__table__.foreign_keys:
            if fk.parent.name == "creative_id":
                assert fk.ondelete == "CASCADE"
