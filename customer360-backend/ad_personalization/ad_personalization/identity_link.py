"""Link a known visitor's devices to one profile reference (task T091, FR-018).

A master profile in the customer data platform lists the browser cookie ids it has
been resolved from (``cdp_master_profiles.cookie_ids``). The ad server's visitor
id (``leovid``) is such a cookie id. This job derives each cookie id's visitor key
(the same keyed hash the ad server uses) and records on
``leo_ads.user_serving_key.profile_ref`` which master profile the key belongs to,
so every key of one person shares one reference. The ad server then builds that
person's view at read time as the union of all keys sharing the reference; nothing
stored is ever merged, so unlinking a key removes its contribution cleanly.

Rules:

* A cookie id counts only for a master profile that has at least one ACTIVE link,
  and not when it came solely from links that are UNLINKED, HISTORICAL or
  SUPERSEDED.
* A cookie id that appears on more than one master profile is ambiguous: it is
  linked to neither and logged, never guessed.
* A key whose link is gone, or that now resolves to nobody, has its ``profile_ref``
  set to NULL. The row and its other data stay. Only references that look like a
  master profile id (a UUID) are ever cleared; any other value is not ours.
* A key that already belongs to ANOTHER ad tenant is left untouched and logged
  (the table's primary key is the key alone, so it cannot be shared).
* A visitor with an open erasure request is never linked.
* Idempotent: a second run with the same data changes nothing.

Only tenants with personalisation switched on at tenant scope are processed. The
visitor key derivation is duplicated from ``customer360-promotions`` and
``customer360-api`` on purpose (each location deploys on its own); the shared
vectors in ``contracts/visitor-key-test-vectors.json`` keep the three in step.
Never log a raw cookie id or a full key; only a fingerprint.

The functions here never commit: the caller owns the transaction.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Any, Callable, Optional

from psycopg2 import sql

from ad_personalization.erasure import OPEN_STATUSES

logger = logging.getLogger(__name__)

KEY_BYTES = 16
MAX_VISITOR_ID_LENGTH = 128
_TENANT_CONTEXT = b"c360ads:tenant:"
_DEVELOPMENT_SECRET = "development-only-visitor-key-secret"
PLATFORM_SCHEMA = "customer360"

_UUID_PATTERN = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


class VisitorKeyUnavailable(RuntimeError):
    """No master secret is configured, so visitor keys cannot be derived."""


def configured_master_secret() -> Optional[str]:
    """The master secret to use, or None when keys must not be derived."""
    for name in ("AD_VISITOR_KEY_MASTER_SECRET", "c360_PROMOTION_VISITOR_KEY_MASTER_SECRET"):
        value = os.environ.get(name)
        if value:
            return value
    environment = os.environ.get("c360_PROMOTION_ENVIRONMENT", "development")
    return _DEVELOPMENT_SECRET if environment == "development" else None


def tenant_secret(master_secret: str, tenant_id: int) -> bytes:
    return hmac.new(
        master_secret.encode("utf-8"),
        _TENANT_CONTEXT + str(int(tenant_id)).encode("ascii"),
        hashlib.sha256,
    ).digest()


def normalise_visitor_id(leovid: str) -> str:
    if not isinstance(leovid, str):
        raise ValueError("visitor id must be a string")
    cleaned = leovid.strip()
    if not cleaned:
        raise ValueError("visitor id must not be empty")
    if len(cleaned) > MAX_VISITOR_ID_LENGTH:
        raise ValueError(f"visitor id is longer than {MAX_VISITOR_ID_LENGTH} characters")
    return cleaned


def visitor_key(tenant_id: int, leovid: str, master_secret: Optional[str] = None) -> bytes:
    """The 16-byte visitor key for one tenant (same derivation as the ad server)."""
    if master_secret is None:
        master_secret = configured_master_secret()
    if not master_secret:
        raise VisitorKeyUnavailable("the visitor key master secret is not configured")
    digest = hmac.new(
        tenant_secret(master_secret, tenant_id),
        normalise_visitor_id(leovid).encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return digest[:KEY_BYTES]


def fingerprint(key: bytes) -> str:
    return bytes(key)[:4].hex()


def resolve_cookie_keys(
    rows: list[tuple[str, str]], tenant_id: int, master_secret: str
) -> tuple[dict[bytes, str], int, int]:
    """Turn ``(master_profile_id, cookie_id)`` pairs into ``{key: profile_ref}``.

    Returns ``(links, ambiguous, invalid)``. A key claimed by two different master
    profiles is dropped (ambiguous); an unusable cookie id is counted and skipped.
    """
    claimed: dict[bytes, set[str]] = {}
    invalid = 0
    for master_id, cookie in rows:
        try:
            key = visitor_key(tenant_id, cookie, master_secret)
        except ValueError:
            invalid += 1
            continue
        claimed.setdefault(key, set()).add(str(master_id))
    links = {k: next(iter(v)) for k, v in claimed.items() if len(v) == 1}
    return links, sum(1 for v in claimed.values() if len(v) > 1), invalid


def link_tenants(conn, schema: str) -> list[tuple[int, str]]:
    """``(ad tenant id, platform tenant id)`` for tenants with personalisation on."""
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                """
                SELECT t.tenant_id, t.customer360_tenant_id::text
                FROM {s}.tenant t
                JOIN {s}.personalization_setting s
                  ON s.tenant_id = t.tenant_id AND s.scope_type = 'tenant' AND s.scope_ref = ''
                WHERE t.customer360_tenant_id IS NOT NULL AND s.enabled
                ORDER BY t.tenant_id
                """
            ).format(s=sql.Identifier(schema))
        )
        return [(int(r[0]), r[1]) for r in cur.fetchall()]


def _cookie_rows(conn, platform_tenant_id: str) -> list[tuple[str, str]]:
    """Active ``(master_profile_id, cookie_id)`` pairs for one platform tenant (RLS context set)."""
    p = sql.Identifier(PLATFORM_SCHEMA)
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (platform_tenant_id,))
        cur.execute(
            sql.SQL(
                """
                SELECT m.master_profile_id::text, c.cookie
                FROM {p}.cdp_master_profiles m
                CROSS JOIN LATERAL unnest(m.cookie_ids) AS c(cookie)
                WHERE m.tenant_id = %(tenant)s::uuid
                  AND EXISTS (
                      SELECT 1 FROM {p}.cdp_profile_links l
                      WHERE l.master_profile_id = m.master_profile_id
                        AND l.tenant_id = m.tenant_id AND l.status = 'ACTIVE')
                  AND (
                      EXISTS (
                          SELECT 1 FROM {p}.cdp_profile_links l
                          JOIN {p}.cdp_raw_profiles_stage r ON r.raw_profile_id = l.raw_profile_id
                          WHERE l.master_profile_id = m.master_profile_id
                            AND l.tenant_id = m.tenant_id AND l.status = 'ACTIVE'
                            AND r.cookie_id = c.cookie)
                      OR NOT EXISTS (
                          SELECT 1 FROM {p}.cdp_profile_links l
                          JOIN {p}.cdp_raw_profiles_stage r ON r.raw_profile_id = l.raw_profile_id
                          WHERE l.master_profile_id = m.master_profile_id
                            AND l.tenant_id = m.tenant_id AND l.status <> 'ACTIVE'
                            AND r.cookie_id = c.cookie)
                  )
                """
            ).format(p=p),
            {"tenant": platform_tenant_id},
        )
        return [(r[0], r[1]) for r in cur.fetchall() if r[1]]


def _open_erasure_keys(conn, schema: str, tenant_id: int) -> set[bytes]:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT user_key_hash FROM {s}.erasure_request "
                "WHERE tenant_id = %s AND status = ANY(%s)"
            ).format(s=sql.Identifier(schema)),
            (tenant_id, list(OPEN_STATUSES)),
        )
        return {bytes(r[0]) for r in cur.fetchall()}


def _link_tenant(
    conn, schema: str, tenant_id: int, platform_tenant_id: str, master_secret: str
) -> dict[str, int]:
    rows = _cookie_rows(conn, platform_tenant_id)
    desired, ambiguous, invalid = resolve_cookie_keys(rows, tenant_id, master_secret)
    for key in _open_erasure_keys(conn, schema, tenant_id) & set(desired):
        del desired[key]
    table = sql.Identifier(schema, "user_serving_key")

    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT user_key_hash, tenant_id, profile_ref FROM {t}").format(t=table)
            + sql.SQL(" WHERE user_key_hash = ANY(%s) OR (tenant_id = %s AND profile_ref ~ %s)"),
            ([bytes(k) for k in desired], tenant_id, _UUID_PATTERN),
        )
        existing = {bytes(r[0]): (int(r[1]), r[2]) for r in cur.fetchall()}

        linked = unlinked = foreign = 0
        for key, ref in desired.items():
            owner = existing.get(key)
            if owner is not None and owner[0] != tenant_id:
                foreign += 1
                logger.error(
                    "identity_link: key %s already belongs to another ad tenant; left untouched",
                    fingerprint(key),
                )
                continue
            if owner is not None and owner[1] == ref:
                continue
            cur.execute(
                sql.SQL(
                    """
                    INSERT INTO {t} (user_key_hash, tenant_id, profile_ref, first_seen_at, last_seen_at)
                    VALUES (%s, %s, %s, now(), now())
                    ON CONFLICT (user_key_hash) DO UPDATE SET profile_ref = EXCLUDED.profile_ref
                    WHERE {t}.tenant_id = EXCLUDED.tenant_id
                    """
                ).format(t=table),
                (key, tenant_id, ref),
            )
            linked += 1

        stale = [
            k for k, (owner_tenant, ref) in existing.items()
            if owner_tenant == tenant_id and ref is not None and k not in desired
        ]
        # Only clear what looks like one of ours (a master profile id).
        for key in stale:
            cur.execute(
                sql.SQL(
                    "UPDATE {t} SET profile_ref = NULL "
                    "WHERE tenant_id = %s AND user_key_hash = %s AND profile_ref ~ %s"
                ).format(t=table),
                (tenant_id, key, _UUID_PATTERN),
            )
            unlinked += cur.rowcount

    if ambiguous:
        logger.warning("identity_link: tenant %s: %d ambiguous cookie id(s) left unlinked", tenant_id, ambiguous)
    return {
        "linked": linked, "unlinked": unlinked, "ambiguous": ambiguous,
        "invalid": invalid, "foreign": foreign,
    }


def link_identities(
    conn,
    *,
    schema: str,
    master_secret: Optional[str] = None,
    log: Callable[[str], None] = logger.info,
) -> dict[int, dict[str, int]]:
    """Link every enabled tenant. Returns ``{ad tenant id: counts}``; never commits."""
    master_secret = master_secret or configured_master_secret()
    if not master_secret:
        logger.error(
            "identity_link: no visitor key master secret is configured "
            "(AD_VISITOR_KEY_MASTER_SECRET); no identities were linked"
        )
        return {}
    summary: dict[int, dict[str, int]] = {}
    for tenant_id, platform_tenant_id in link_tenants(conn, schema):
        summary[tenant_id] = _link_tenant(conn, schema, tenant_id, platform_tenant_id, master_secret)
        log(f"identity_link: tenant {tenant_id}: {summary[tenant_id]}")
    return summary
