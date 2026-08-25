"""Redis Streams -> Postgres event Loader for the web-tracking pipeline.

Consumes the broker Redis stream produced by ``data-tracking-api`` (each accepted
event is XADD-ed by ``data-tracking-api/core/redis_cache.py`` ``EventStreamPublisher``)
using a **consumer group**, and lands events into ``customer360.cdp_raw_events``.
Design reference: ``deployments/docs/web-tracking-implementation-plan.md`` §6-8.

Connection env (set by ``deployments/server/deploy-backend.sh``):
  BROKER_REDIS_HOST / BROKER_REDIS_PORT / BROKER_REDIS_PASSWORD / BROKER_REDIS_DB
  DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD / DB_SCHEMA   (Postgres target)
Tuning env:
  EVENT_LOADER_STREAM_KEY  (default cdp:events:raw)   EVENT_LOADER_GROUP (default loader)
  EVENT_LOADER_CONSUMER    (default $HOSTNAME|loader-1) EVENT_LOADER_BATCH (default 500)
  EVENT_LOADER_BLOCK_MS    (default 5000)  EVENT_LOADER_MIN_IDLE_MS (default 60000)

PERSISTENCE IS GATED. By default the loader consumes + parses + ACKs and logs counts
(a runnable, observable pipeline) WITHOUT writing to Postgres. Set
``EVENT_LOADER_WRITE_EVENTS=true`` AND ``EVENT_LOADER_TENANT_ID=<tenant uuid>`` to land
rows. Landing needs two business decisions the CDP team owns (marked TODO below):
  1. data_source_id -> tenant_id (+ domain) mapping. This scaffold uses one env tenant.
  2. identity -> cdp_raw_profiles_stage resolution. cdp_raw_events.raw_profile_id is
     NOT NULL, so a stage row must exist first; this scaffold stages one row per event
     (NO identity dedup — real CIR keying is a TODO).
Idempotency: at-least-once (XACK only after the DB commit). Exactly-once needs a unique
constraint on cdp_raw_events; the table is partitioned by event_time, so any unique index
must include event_time (Postgres rule) — a schema decision left to the team.
"""

import json
import logging
import os
import socket
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

STREAM_ID_FIELD = "__stream_id__"  # internal: carries the Redis entry id alongside the parsed payload


@dataclass(frozen=True)
class LoaderConfig:
    redis_host: str
    redis_port: int
    redis_password: Optional[str]
    redis_db: int
    stream_key: str
    group: str
    consumer: str
    batch: int
    block_ms: int
    min_idle_ms: int
    write_events: bool
    tenant_id: Optional[str]
    domain: str
    source_system: str
    db_schema: str

    @classmethod
    def from_env(cls) -> "LoaderConfig":
        return cls(
            redis_host=os.environ.get("BROKER_REDIS_HOST", "127.0.0.1"),
            redis_port=int(os.environ.get("BROKER_REDIS_PORT", "6580")),
            redis_password=os.environ.get("BROKER_REDIS_PASSWORD") or None,
            redis_db=int(os.environ.get("BROKER_REDIS_DB", "0")),
            stream_key=os.environ.get("EVENT_LOADER_STREAM_KEY", "cdp:events:raw"),
            group=os.environ.get("EVENT_LOADER_GROUP", "loader"),
            consumer=os.environ.get("EVENT_LOADER_CONSUMER") or socket.gethostname() or "loader-1",
            batch=int(os.environ.get("EVENT_LOADER_BATCH", "500")),
            block_ms=int(os.environ.get("EVENT_LOADER_BLOCK_MS", "5000")),
            min_idle_ms=int(os.environ.get("EVENT_LOADER_MIN_IDLE_MS", "60000")),
            write_events=os.environ.get("EVENT_LOADER_WRITE_EVENTS", "false").lower() == "true",
            tenant_id=os.environ.get("EVENT_LOADER_TENANT_ID") or None,
            domain=os.environ.get("EVENT_LOADER_DOMAIN", "retail"),
            source_system=os.environ.get("EVENT_LOADER_SOURCE_SYSTEM", "WebTracking"),
            db_schema=os.environ.get("DB_SCHEMA") or os.environ.get("DB_NAME", "customer360"),
        )


def build_redis_client(cfg: LoaderConfig) -> Any:
    import redis  # imported lazily so the module imports without redis installed (defs load in CI)

    return redis.Redis(
        host=cfg.redis_host,
        port=cfg.redis_port,
        password=cfg.redis_password,
        db=cfg.redis_db,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=5,
        health_check_interval=30,
    )


def ensure_group(client: Any, stream_key: str, group: str) -> None:
    """Create the consumer group (and the stream) if it does not exist yet."""
    try:
        client.xgroup_create(name=stream_key, groupname=group, id="0", mkstream=True)
    except Exception as exc:  # redis.ResponseError('BUSYGROUP ...') when it already exists
        if "BUSYGROUP" not in str(exc):
            raise


def parse_entry(fields: dict[str, Any]) -> dict[str, Any]:
    """Turn a stream entry's fields into the event envelope written by the collector."""
    payload = fields.get("payload")
    if payload:
        try:
            return json.loads(payload)
        except (TypeError, ValueError):
            logger.warning("event_loader: unparseable payload; keeping raw fields")
    # Fallback: the flat fields carry data_source_id/received_at even without a payload.
    return {k: v for k, v in fields.items() if k != "payload"}


def read_batch(client: Any, cfg: LoaderConfig) -> list[tuple[str, dict[str, Any]]]:
    """Reclaim stuck-pending entries, then read new ones. Returns (entry_id, fields) pairs."""
    out: list[tuple[str, dict[str, Any]]] = []
    try:
        claimed = client.xautoclaim(
            cfg.stream_key, cfg.group, cfg.consumer, cfg.min_idle_ms, start_id="0-0", count=cfg.batch
        )
        # redis-py returns (next_cursor, messages, deleted); older shapes omit `deleted`.
        messages = claimed[1] if isinstance(claimed, (list, tuple)) and len(claimed) >= 2 else []
        out.extend(messages or [])
    except Exception:
        logger.debug("event_loader: xautoclaim skipped", exc_info=True)
    if len(out) < cfg.batch:
        resp = client.xreadgroup(
            cfg.group, cfg.consumer, {cfg.stream_key: ">"}, count=cfg.batch - len(out), block=cfg.block_ms
        )
        for _stream, messages in resp or []:
            out.extend(messages)
    return out


def persist_batch(pg_conn: Any, cfg: LoaderConfig, envelopes: list[tuple[str, dict[str, Any]]]) -> int:
    """Land events into cdp_raw_events (staging a raw profile per event first).

    Requires cfg.tenant_id. Runs in one transaction; the caller commits then ACKs, so a
    crash before ACK re-delivers the batch (at-least-once). See the module docstring for the
    two business TODOs (tenant mapping + identity dedup) this deliberately does NOT decide.
    """
    if not cfg.tenant_id:
        raise ValueError("EVENT_LOADER_TENANT_ID is required when EVENT_LOADER_WRITE_EVENTS=true")
    schema = cfg.db_schema
    written = 0
    with pg_conn.cursor() as cur:
        for entry_id, env in envelopes:
            event = env.get("event") or {}
            received_at = env.get("received_at")
            cookie_id = _s(event.get("cookie_id"))
            session_id = _s(event.get("session_id"))
            user_ext = _s(event.get("user_id"))
            # 1) Stage a raw profile (NO identity dedup yet -- TODO: resolve/reuse by identity key).
            cur.execute(
                f"""
                INSERT INTO {schema}.cdp_raw_profiles_stage
                    (tenant_id, domain, source_system, channel, cookie_id, session_id, external_customer_id)
                VALUES (%s, %s, %s, 'web', %s, %s, %s)
                RETURNING raw_profile_id
                """,
                (cfg.tenant_id, cfg.domain, cfg.source_system, cookie_id, session_id, user_ext),
            )
            raw_profile_id = cur.fetchone()[0]
            # 2) Insert the event (event_dedup_key = stream id for traceability / future dedup).
            cur.execute(
                f"""
                INSERT INTO {schema}.cdp_raw_events
                    (tenant_id, raw_profile_id, source_system, domain, channel,
                     external_customer_id, cookie_id, session_id,
                     event_category, event_name, event_time, event_payload, event_dedup_key)
                VALUES (%s, %s, %s, %s, 'web', %s, %s, %s,
                        %s, %s, COALESCE(%s::timestamptz, now()), %s::jsonb, %s)
                """,
                (
                    cfg.tenant_id, raw_profile_id, cfg.source_system, cfg.domain,
                    user_ext, cookie_id, session_id,
                    "GENERAL", _event_name(event), received_at, json.dumps(event), entry_id,
                ),
            )
            written += 1
    return written


def run_loader_once(*, client: Any = None, pg_conn_factory=None) -> int:
    """One drain cycle: reclaim + read a batch, optionally land it, then ACK. Returns count."""
    cfg = LoaderConfig.from_env()
    client = client or build_redis_client(cfg)
    ensure_group(client, cfg.stream_key, cfg.group)

    entries = read_batch(client, cfg)
    if not entries:
        return 0
    envelopes = [(entry_id, parse_entry(fields)) for entry_id, fields in entries]

    if cfg.write_events:
        conn = (pg_conn_factory or (lambda: _pg_connect(cfg)))()
        try:
            persist_batch(conn, cfg, envelopes)
            conn.commit()
        finally:
            conn.close()
    else:
        logger.info(
            "event_loader: consumed %d event(s) from %s (persistence OFF -- set "
            "EVENT_LOADER_WRITE_EVENTS=true + EVENT_LOADER_TENANT_ID to land them)",
            len(entries), cfg.stream_key,
        )

    client.xack(cfg.stream_key, cfg.group, *[entry_id for entry_id, _ in entries])
    return len(entries)


def _pg_connect(cfg: LoaderConfig) -> Any:
    import psycopg2  # lazy import (see build_redis_client rationale)

    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", "5432")),
        dbname=os.environ.get("DB_NAME", "customer360"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", ""),
    )


def _event_name(event: dict[str, Any]) -> str:
    return _s(event.get("event_name")) or _s(event.get("name")) or "page-view"


def _s(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None
