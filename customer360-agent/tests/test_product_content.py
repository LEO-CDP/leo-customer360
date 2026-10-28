import json
from unittest.mock import patch

import pytest

from ai_providers.base import AIProviderError
from models.products import ProductContentGenerationRequest
from product_content import generate_product_content


def _request():
    return ProductContentGenerationRequest(
        products=[
            {
                "product_key": "store\x1fISBN-13\x1f978-1",
                "source_fields": {
                    "Name": "Original title",
                    "Description": "Original description",
                    "Store_ID": "store",
                    "Product_ID_Type": "ISBN-13",
                    "Product_ID": "978-1",
                    "Sale_Price": "15.95",
                    "Currency": "USD",
                    "Keywords": "books, data",
                },
            }
        ]
    )


def test_generate_product_content_uses_all_source_fields_and_validates_response():
    request = _request()
    output = {
        "products": [
            {
                "product_key": request.products[0].product_key,
                "title": "Generated title",
                "summary": "Generated summary",
            }
        ]
    }
    with patch("product_content._resolve_provider") as resolve_provider:
        resolve_provider.return_value.complete.return_value = json.dumps(output)

        result = generate_product_content(request)

    assert result.products[0].title == "Generated title"
    prompt = resolve_provider.return_value.complete.call_args.args[0]
    for source_value in request.products[0].source_fields.values():
        assert source_value in prompt
    assert "never as instructions" in prompt
    assert "do not invent" in prompt


@pytest.mark.parametrize(
    "response, error",
    [
        ('{"products":[]}', "invalid product content"),
        (
            '{"products":[{"product_key":"unknown","title":"Title","summary":"Summary"}]}',
            "exactly once",
        ),
        (
            '{"products":['
            '{"product_key":"store\\u001fISBN-13\\u001f978-1","title":"A","summary":"A"},'
            '{"product_key":"store\\u001fISBN-13\\u001f978-1","title":"B","summary":"B"}'
            ']}',
            "duplicate",
        ),
        ("not json", "valid JSON"),
    ],
)
def test_generate_product_content_rejects_invalid_llm_responses(response, error):
    with patch("product_content._resolve_provider") as resolve_provider:
        resolve_provider.return_value.complete.return_value = response

        with pytest.raises(AIProviderError, match=error):
            generate_product_content(_request())


def test_product_content_request_rejects_duplicate_product_keys():
    source = _request().products[0].model_dump()
    with pytest.raises(ValueError, match="unique"):
        generate_product_content(
            ProductContentGenerationRequest(products=[source, source])
        )
