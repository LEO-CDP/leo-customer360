import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text

from core.repositories.product_item_repository import ProductItemRepository
from leo_customer360_dao.schemas.product_item import ProductItemCreate, ProductItemUpdate

TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def make_payload():
    return ProductItemCreate(
        domain="retail",
        product_type="retail_product",
        source_id="merchant-1",
        product_id_type="SKU",
        product_id="sku-1",
        title="A product",
        source_type="",
        ext_attributes={"product_category": "book"},
    )


def test_create_product_writes_linked_content_and_product_in_one_transaction():
    session = MagicMock()
    repository = ProductItemRepository(session)
    with patch.object(repository, "get_product", return_value={"product_id": "sku-1"}) as read:
        result = repository.create_product(tenant_id=TENANT_ID, payload=make_payload())

    assert result == {"product_id": "sku-1"}
    session.add.assert_called_once()
    session.flush.assert_called_once()
    session.execute.assert_called_once()
    statement, params = session.execute.call_args.args
    assert isinstance(statement, type(text("SELECT 1")))
    assert "INSERT INTO" in str(statement)
    assert "cdp_product_items" in str(statement)
    assert params["tenant_id"] == TENANT_ID
    assert params["source_id"] == "merchant-1"
    assert params["source_type"] == ""
    assert params["ext_attributes"] == '{"product_category": "book"}'
    session.commit.assert_called_once()
    session.rollback.assert_not_called()
    read.assert_called_once()
    assert read.call_args.kwargs["tenant_id"] == TENANT_ID
    assert read.call_args.kwargs["product_item_id"]
    assert isinstance(read.call_args.kwargs["product_item_id"], str)


def test_create_product_rolls_back_if_product_metadata_insert_fails():
    session = MagicMock()
    session.execute.side_effect = RuntimeError("insert failed")
    repository = ProductItemRepository(session)

    with pytest.raises(RuntimeError, match="insert failed"):
        repository.create_product(tenant_id=TENANT_ID, payload=make_payload())

    session.rollback.assert_called_once()
    session.commit.assert_not_called()


def test_product_listing_query_always_includes_tenant_filter():
    session = MagicMock()
    session.execute.return_value.mappings.return_value.all.return_value = []
    repository = ProductItemRepository(session)

    assert repository.list_products(
        tenant_id=TENANT_ID,
        skip=0,
        limit=25,
        q="book",
        tag="featured",
    ) == []

    statement, params = session.execute.call_args.args
    assert "product.tenant_id = :tenant_id" in str(statement)
    assert "LEFT JOIN" in str(statement)
    assert "COALESCE(content.title, product.source_fields->>'Name')" in str(statement)
    assert "SELECT *" not in str(statement)
    assert params["tenant_id"] == TENANT_ID
    assert params["search"] == "%book%"
    assert params["tag"] == "%featured%"


def test_update_product_updates_linked_content_and_product_for_tenant():
    session = MagicMock()
    session.execute.return_value.first.return_value = (uuid.uuid4(),)
    repository = ProductItemRepository(session)
    payload = ProductItemUpdate(
        **make_payload().model_dump(),
        status_code=0,
    )
    with patch.object(repository, "get_product", return_value={"product_id": "sku-1"}) as read:
        result = repository.update_product(
            tenant_id=TENANT_ID,
            product_item_id="product-1",
            payload=payload,
        )

    assert result == {"product_id": "sku-1"}
    assert session.execute.call_count == 3
    content_statement, content_params = session.execute.call_args_list[1].args
    product_statement, product_params = session.execute.call_args_list[2].args
    assert "UPDATE" in str(content_statement)
    assert "tenant_id = :tenant_id" in str(content_statement)
    assert content_params["status_code"] == 0
    assert "UPDATE" in str(product_statement)
    assert "tenant_id = :tenant_id" in str(product_statement)
    assert product_params["tenant_id"] == TENANT_ID
    assert product_params["product_item_id"] == "product-1"
    assert product_params["source_id"] == "merchant-1"
    assert product_params["source_fields"]
    session.commit.assert_called_once()
    session.rollback.assert_not_called()
    read.assert_called_once_with(tenant_id=TENANT_ID, product_item_id="product-1")


def test_update_product_returns_none_for_unknown_tenant_product():
    session = MagicMock()
    session.execute.return_value.first.return_value = None
    repository = ProductItemRepository(session)

    result = repository.update_product(
        tenant_id=TENANT_ID,
        product_item_id="missing",
        payload=ProductItemUpdate(**make_payload().model_dump()),
    )

    assert result is None
    session.commit.assert_not_called()
    session.rollback.assert_called_once()
