"""Creative selection options demonstrated by the promotions sample catalog."""

AD_TYPES: tuple[tuple[str, str], ...] = (
    ("display", "Display"),
    ("dynamic_retargeting", "Dynamic retargeting"),
    ("sponsored_content", "Sponsored content"),
    ("native", "Native"),
    ("affiliate", "Affiliate"),
)

FORMAT_CODES: tuple[tuple[str, str], ...] = (
    ("single_banner", "Single banner"),
    ("product_carousel", "Product carousel"),
    ("native", "Native"),
    ("native_product", "Native product"),
    ("google_ads", "Google Ads (provider setup required)"),
    ("external_widget", "External widget (provider setup required)"),
)


def creative_options() -> dict[str, list[dict[str, str]]]:
    """Return selectable examples without restricting the extensible API."""
    return {
        "ad_types": [{"value": value, "label": label} for value, label in AD_TYPES],
        "format_codes": [{"value": value, "label": label} for value, label in FORMAT_CODES],
    }
