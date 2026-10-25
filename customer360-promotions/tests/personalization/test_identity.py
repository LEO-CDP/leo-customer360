"""Visitor key derivation: stable, tenant-scoped, keyed, and never the raw id."""

import hashlib
import hmac

import pytest

from personalization.identity import (
    KEY_BYTES,
    MAX_VISITOR_ID_LENGTH,
    VisitorKeyUnavailable,
    fingerprint,
    visitor_key,
)

SECRET = "unit-test-master-secret"


def test_key_is_stable_and_has_the_documented_length():
    first = visitor_key(1, "visitor-1", SECRET)
    assert first == visitor_key(1, "visitor-1", SECRET)
    assert isinstance(first, bytes)
    assert len(first) == KEY_BYTES == 16


def test_the_derivation_matches_the_documented_formula():
    """tenant_secret = HMAC(master, 'c360ads:tenant:<id>'); key = HMAC(tenant_secret, id)[:16]."""
    tenant_secret = hmac.new(SECRET.encode(), b"c360ads:tenant:7", hashlib.sha256).digest()
    expected = hmac.new(tenant_secret, b"visitor-1", hashlib.sha256).digest()[:16]
    assert visitor_key(7, "visitor-1", SECRET) == expected


def test_the_same_visitor_gets_a_different_key_in_every_tenant():
    assert visitor_key(1, "visitor-1", SECRET) != visitor_key(2, "visitor-1", SECRET)


def test_different_visitors_get_different_keys():
    assert visitor_key(1, "visitor-1", SECRET) != visitor_key(1, "visitor-2", SECRET)


def test_a_different_master_secret_gives_a_different_key():
    assert visitor_key(1, "visitor-1", SECRET) != visitor_key(1, "visitor-1", "another-secret")


def test_surrounding_whitespace_is_ignored():
    assert visitor_key(1, "  visitor-1\n", SECRET) == visitor_key(1, "visitor-1", SECRET)


def test_the_raw_visitor_id_never_appears_in_the_key():
    key = visitor_key(1, "visitor-secret-id", SECRET)
    assert b"visitor-secret-id" not in key
    assert "visitor-secret-id" not in key.hex()


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "x" * (MAX_VISITOR_ID_LENGTH + 1), None, 123],
)
def test_unusable_visitor_ids_are_rejected(bad):
    with pytest.raises(ValueError):
        visitor_key(1, bad, SECRET)


def test_a_visitor_id_at_the_length_limit_is_accepted():
    assert len(visitor_key(1, "x" * MAX_VISITOR_ID_LENGTH, SECRET)) == KEY_BYTES


def test_development_falls_back_to_a_built_in_secret(monkeypatch):
    from core.config import promotions_settings

    monkeypatch.setattr(promotions_settings, "environment", "development")
    monkeypatch.setattr(promotions_settings, "visitor_key_master_secret", None)
    assert len(visitor_key(1, "visitor-1")) == KEY_BYTES


def test_outside_development_a_missing_secret_means_no_keys(monkeypatch):
    from core.config import promotions_settings

    monkeypatch.setattr(promotions_settings, "environment", "production")
    monkeypatch.setattr(promotions_settings, "visitor_key_master_secret", None)
    with pytest.raises(VisitorKeyUnavailable):
        visitor_key(1, "visitor-1")


def test_a_configured_secret_is_used_in_production(monkeypatch):
    from core.config import promotions_settings

    monkeypatch.setattr(promotions_settings, "environment", "production")
    monkeypatch.setattr(promotions_settings, "visitor_key_master_secret", SECRET)
    assert visitor_key(1, "visitor-1") == visitor_key(1, "visitor-1", SECRET)


def test_the_shared_vectors_match_so_the_admin_api_derives_the_same_keys():
    """customer360-api (core/utils/ad_visitor_key.py) loads the same file; neither can drift alone."""
    import json
    from pathlib import Path

    from personalization.identity import tenant_secret

    path = (
        Path(__file__).resolve().parents[3]
        / "docs" / "ad-personalization" / "contracts" / "visitor-key-test-vectors.json"
    )
    if not path.exists():
        pytest.skip("shared test vectors not available in this checkout")
    vectors = json.loads(path.read_text())

    for case in vectors["vectors"]:
        assert tenant_secret(case["master_secret"], case["tenant_id"]).hex() == case["tenant_secret_hex"]
        key = visitor_key(case["tenant_id"], case["leovid"], case["master_secret"])
        assert key.hex() == case["expected_key_hex"]
    for bad in vectors["invalid_visitor_ids"]:
        with pytest.raises(ValueError):
            visitor_key(1, bad, "s")


def test_fingerprint_is_short_and_safe_for_none():
    key = visitor_key(1, "visitor-1", SECRET)
    assert fingerprint(key) == key[:4].hex()
    assert len(fingerprint(key)) == 8
    assert fingerprint(None) is None
