"""Ad feature dimensions: what a visitor's behaviour is learned over."""

from personalization.features import (
    DIM_AD_TYPE,
    DIM_ADVERTISER,
    DIM_FORMAT,
    DIM_TAG,
    DIMENSIONS,
    MAX_TAG_LENGTH,
    MAX_TAGS,
    ad_features,
    shared_features,
)


def test_the_dimensions_are_the_documented_four():
    assert DIMENSIONS == ("advertiser", "ad_type", "format", "tag")


def test_an_ad_is_described_by_advertiser_type_format_and_tags():
    ad = {
        "advertiser_id": 7,
        "ad_type": "native",
        "format_code": "native_product",
        "ad_metadata": {"tags": ["Fashion", "sale"]},
    }
    assert ad_features(ad) == [
        (DIM_ADVERTISER, "7"),
        (DIM_AD_TYPE, "native"),
        (DIM_FORMAT, "native_product"),
        (DIM_TAG, "fashion"),
        (DIM_TAG, "sale"),
    ]


def test_missing_values_are_skipped_not_invented():
    assert ad_features({}) == []
    assert ad_features({"advertiser_id": None, "ad_type": "", "format_code": None}) == []
    assert ad_features({"ad_type": "display"}) == [(DIM_AD_TYPE, "display")]


def test_tags_fall_back_to_the_creative_payload():
    ad = {"ad_metadata": {}, "content_payload": {"tags": ["Menswear"]}}
    assert ad_features(ad) == [(DIM_TAG, "menswear")]


def test_ad_metadata_tags_win_over_the_creative_payload():
    ad = {"ad_metadata": {"tags": ["a"]}, "content_payload": {"tags": ["b"]}}
    assert ad_features(ad) == [(DIM_TAG, "a")]


def test_tags_are_cleaned_deduplicated_and_bounded():
    ad = {"ad_metadata": {"tags": ["  Sale ", "sale", "SALE", 5, None, "", "x" * 200]}}
    tags = [value for _dim, value in ad_features(ad)]
    assert tags == ["sale", "x" * MAX_TAG_LENGTH]

    many = {"ad_metadata": {"tags": [f"t{i}" for i in range(50)]}}
    assert len(ad_features(many)) == MAX_TAGS


def test_tags_that_are_not_a_list_are_ignored():
    assert ad_features({"ad_metadata": {"tags": "fashion"}}) == []
    assert ad_features({"ad_metadata": None, "content_payload": None}) == []


def test_ads_share_the_features_they_have_in_common():
    first = [(DIM_ADVERTISER, "7"), (DIM_TAG, "sale")]
    second = [(DIM_ADVERTISER, "7"), (DIM_TAG, "new")]
    assert shared_features(first, second) == {(DIM_ADVERTISER, "7")}
    assert shared_features(first, []) == set()
