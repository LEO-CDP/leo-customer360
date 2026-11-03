"""Tenant-scoped campaign audience synchronization.

The job exports only eligible, matched ``cdp_master_profiles`` identifiers.
External provider identifiers are normalized and SHA-256 hashed; LEO Ads
stores a tenant-scoped serving hash and the profile UUID reference. Provider
credentials and endpoint versions live in the tenant's ADS connector row;
this module never logs credentials or raw profile identifiers.
"""

from __future__ import annotations

import csv
import hashlib
import io
import itertools
import json
import logging
import os
import re
import uuid
from contextlib import closing
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor, execute_values

load_dotenv()

DB_SCHEMA = os.environ.get("DB_SCHEMA", "customer360")
logger = logging.getLogger(__name__)
SUPPORTED_PLATFORMS = frozenset({"meta_ads", "google_ads", "tiktok_ads", "leo_ads"})
PLATFORM_CONSENT_KEYS = {
    "meta_ads": "meta_ads_opt_in",
    "google_ads": "google_ads_opt_in",
    "tiktok_ads": "tiktok_ads_opt_in",
    "leo_ads": "leo_ads_opt_in",
}
PLATFORM_ALIASES = {
    "facebook": "meta_ads",
    "facebook_ads": "meta_ads",
    "fb": "meta_ads",
    "meta": "meta_ads",
    "meta_ads": "meta_ads",
    "google": "google_ads",
    "google_ads": "google_ads",
    "tiktok": "tiktok_ads",
    "tiktok_ads": "tiktok_ads",
    "leo_ads": "leo_ads",
}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AudienceSyncError(RuntimeError):
    """Raised when an audience sync cannot be completed safely."""


@dataclass(frozen=True)
class AudienceMember:
    """A profile reference and hashed identifiers for an audience export."""

    profile_id: str
    email_hash: str | None
    phone_hash: str | None


@dataclass(frozen=True)
class ProviderResult:
    """Provider identifiers and counts returned by one audience upload."""

    audience_id: str
    uploaded_count: int
    request_id: str | None = None
    metadata: dict[str, Any] | None = None


class AudienceProvider(Protocol):
    """Provider contract used by the Dagster op and unit tests."""

    def sync(
        self,
        audience_name: str,
        members: list[AudienceMember],
        external_audience_id: str | None = None,
    ) -> ProviderResult:
        """Create/update one external audience and upload its members."""
        ...


def _has_explicit_platform_consent(profile: dict[str, Any], platform: str) -> bool:
    """Require literal-true consent for this exact advertising provider."""
    consent_key = PLATFORM_CONSENT_KEYS.get(platform)
    if consent_key is None:
        raise ValueError(f"Unsupported advertising platform consent key: {platform}")
    preferences = profile.get("communication_preferences")
    return (
        isinstance(preferences, dict)
        and preferences.get(consent_key) is True
    )


class HttpClient:
    """Small injectable JSON/multipart HTTP client with bounded responses."""

    def __init__(self, timeout_seconds: float = 30.0):
        self.timeout_seconds = timeout_seconds

    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> tuple[dict[str, Any], dict[str, str]]:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return self._post(url, body, {**headers, "Content-Type": "application/json"})

    def post_multipart(
        self,
        url: str,
        fields: dict[str, str],
        file_name: str,
        file_body: bytes,
        headers: dict[str, str],
    ) -> tuple[dict[str, Any], dict[str, str]]:
        boundary = "----c360-" + uuid.uuid4().hex
        chunks: list[bytes] = []
        for key, value in fields.items():
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
                    str(value).encode(),
                    b"\r\n",
                ]
            )
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'.encode(),
                b"Content-Type: text/csv\r\n\r\n",
                file_body,
                b"\r\n",
                f"--{boundary}--\r\n".encode(),
            ]
        )
        return self._post(
            url,
            b"".join(chunks),
            {**headers, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        )

    def _post(
        self,
        url: str,
        body: bytes,
        headers: dict[str, str],
    ) -> tuple[dict[str, Any], dict[str, str]]:
        request = Request(url, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read(2_000_000)
                return _decode_json(raw), dict(response.headers.items())
        except HTTPError as exc:
            detail = exc.read(4_096).decode("utf-8", errors="replace")
            raise AudienceSyncError(f"Audience provider returned HTTP {exc.code}: {detail[:500]}") from exc
        except URLError as exc:
            raise AudienceSyncError(f"Audience provider request failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise AudienceSyncError("Audience provider request timed out") from exc


class MetaAudienceProvider:
    """Meta/Facebook Custom Audiences customer-list adapter."""

    def __init__(self, config: dict[str, Any], credentials: dict[str, Any], http: HttpClient):
        self.config, self.credentials, self.http = config, credentials, http

    def sync(
        self,
        audience_name: str,
        members: list[AudienceMember],
        external_audience_id: str | None = None,
    ) -> ProviderResult:
        token = _required(self.credentials, "access_token")
        account_id = _required(self.config, "ad_account_id")
        base_url = str(self.config.get("base_url", "https://graph.facebook.com")).rstrip("/")
        version = str(self.config.get("api_version", "v21.0")).strip("/")
        auth = {"Authorization": f"Bearer {token}"}
        audience_id = external_audience_id
        request_id = None
        if not audience_id:
            response, headers = self.http.post_json(
                f"{base_url}/{version}/act_{account_id}/customaudiences",
                {
                    "name": audience_name,
                    "subtype": "CUSTOM",
                    "customer_file_source": "USER_PROVIDED_ONLY",
                },
                auth,
            )
            audience_id = _response_id(response, "Meta audience")
            request_id = headers.get("x-fb-trace-id")
        for batch in _chunks(members, int(self.config.get("batch_size", 10_000))):
            data = [[member.email_hash or "", member.phone_hash or ""] for member in batch]
            response, headers = self.http.post_json(
                f"{base_url}/{version}/{audience_id}/users",
                {"payload": {"schema": ["EMAIL", "PHONE"], "data": data, "is_raw": False}},
                auth,
            )
            request_id = headers.get("x-fb-trace-id", request_id)
            if response.get("error"):
                raise AudienceSyncError("Meta rejected the audience member upload")
        return ProviderResult(audience_id, len(members), request_id)


class GoogleAdsAudienceProvider:
    """Google Ads Customer Match adapter using OfflineUserDataJob REST calls.

    A connector must provide ``customer_id`` and ``user_list_resource``. Google
    Ads now restricts new Customer Match integrations, so the resource is
    intentionally explicit rather than silently creating an unmanaged list.
    """

    def __init__(self, config: dict[str, Any], credentials: dict[str, Any], http: HttpClient):
        self.config, self.credentials, self.http = config, credentials, http

    def sync(
        self,
        audience_name: str,
        members: list[AudienceMember],
        external_audience_id: str | None = None,
    ) -> ProviderResult:
        token = _required(self.credentials, "access_token")
        customer_id = _required(self.config, "customer_id")
        user_list = external_audience_id or _required(self.config, "user_list_resource")
        base_url = str(self.config.get("base_url", "https://googleads.googleapis.com")).rstrip("/")
        version = str(self.config.get("api_version", "v19")).strip("/")
        headers = {
            "Authorization": f"Bearer {token}",
            "developer-token": _required(self.credentials, "developer_token"),
        }
        job_response, response_headers = self.http.post_json(
            f"{base_url}/{version}/customers/{customer_id}/offlineUserDataJobs",
            {
                "job": {
                    "type": "CUSTOMER_MATCH_USER_LIST",
                    "customerMatchUserListMetadata": {"userList": user_list},
                }
            },
            headers,
        )
        job_resource = _nested_value(job_response, "resourceName", "resource_name")
        if not job_resource:
            raise AudienceSyncError("Google Ads did not return an OfflineUserDataJob resource")
        job_path = str(job_resource).split("/customers/", 1)[-1]
        for batch in _chunks(members, min(int(self.config.get("batch_size", 100_000)), 100_000)):
            operations = []
            for member in batch:
                identifiers = []
                if member.email_hash:
                    identifiers.append({"hashedEmail": member.email_hash})
                if member.phone_hash:
                    identifiers.append({"hashedPhoneNumber": member.phone_hash})
                if identifiers:
                    operations.append({"create": {"userIdentifiers": identifiers}})
            self.http.post_json(
                f"{base_url}/{version}/customers/{customer_id}/offlineUserDataJobs/{job_path}:addOfflineUserDataJobOperations",
                {"operations": operations, "enablePartialFailure": True},
                headers,
            )
        run_response, run_headers = self.http.post_json(
            f"{base_url}/{version}/customers/{customer_id}/offlineUserDataJobs/{job_path}:run",
            {},
            headers,
        )
        return ProviderResult(
            user_list,
            len(members),
            run_headers.get("request-id", response_headers.get("request-id")),
            {"offline_user_data_job": job_resource, "run": run_response},
        )


class TikTokAudienceProvider:
    """TikTok CRM custom-audience file upload adapter."""

    def __init__(self, config: dict[str, Any], credentials: dict[str, Any], http: HttpClient):
        self.config, self.credentials, self.http = config, credentials, http

    def sync(
        self,
        audience_name: str,
        members: list[AudienceMember],
        external_audience_id: str | None = None,
    ) -> ProviderResult:
        token = _required(self.credentials, "access_token")
        advertiser_id = _required(self.config, "advertiser_id")
        base_url = str(self.config.get("base_url", "https://business-api.tiktok.com/open_api/v1.3")).rstrip("/")
        upload_path = str(self.config.get("upload_path", "/dmp/custom_audience/file/upload/"))
        create_path = str(self.config.get("create_path", "/dmp/custom_audience/create/"))
        csv_body = _member_csv(members)
        headers = {"Access-Token": token}
        upload, upload_headers = self.http.post_multipart(
            f"{base_url}/{upload_path.strip('/')}",
            {"advertiser_id": advertiser_id},
            "customer360-audience.csv",
            csv_body,
            headers,
        )
        file_id = _nested_value(upload, "file_id", "data", "file_id")
        if not file_id:
            raise AudienceSyncError("TikTok did not return an audience file ID")
        if external_audience_id:
            audience_id = external_audience_id
        else:
            created, created_headers = self.http.post_json(
                f"{base_url}/{create_path.strip('/')}",
                {
                    "advertiser_id": advertiser_id,
                    "audience_name": audience_name,
                    "file_id": file_id,
                    "audience_type": "CUSTOM",
                },
                headers,
            )
            audience_id = _nested_value(created, "audience_id", "data", "audience_id")
            if not audience_id:
                raise AudienceSyncError("TikTok did not return a custom audience ID")
            upload_headers.update(created_headers)
        return ProviderResult(audience_id, len(members), upload_headers.get("request-id"))


def normalize_platform(value: str) -> str:
    """Return a supported platform code or raise a clear configuration error."""
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    platform = PLATFORM_ALIASES.get(key)
    if platform is None:
        raise AudienceSyncError(
            f"Unsupported audience platform {value!r}; expected Meta, Google Ads, TikTok Ads, or LEO Ads"
        )
    return platform


def _required(values: dict[str, Any], key: str) -> str:
    value = str(values.get(key) or "").strip()
    if not value:
        raise AudienceSyncError(f"ADS connector is missing {key}")
    return value


def _decode_json(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AudienceSyncError("Audience provider returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise AudienceSyncError("Audience provider returned a non-object response")
    return value


def _response_id(response: dict[str, Any], provider: str) -> str:
    value = response.get("id") or _nested_value(response, "data", "id")
    if not value:
        raise AudienceSyncError(f"{provider} did not return an audience ID")
    return str(value)


def _nested_value(value: dict[str, Any], *path: str) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _chunks(values: list[AudienceMember], size: int) -> Iterable[list[AudienceMember]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _member_csv(members: list[AudienceMember]) -> bytes:
    stream = io.StringIO()
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["email", "phone"])
    writer.writerows([[member.email_hash or "", member.phone_hash or ""] for member in members])
    return stream.getvalue().encode("utf-8")


def _hash_identifier(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_email(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    return _hash_identifier(normalized) if _EMAIL_RE.fullmatch(normalized) else None


def _normalize_phone(value: Any, default_country_code: str | None = None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+"):
        normalized = "+" + digits
    elif default_country_code:
        normalized = "+" + re.sub(r"\D", "", default_country_code) + digits.lstrip("0")
    else:
        return None
    return _hash_identifier(normalized) if len(digits) >= 8 else None


def build_provider(
    platform: str,
    config: dict[str, Any],
    credentials: dict[str, Any],
    http: HttpClient | None = None,
) -> AudienceProvider:
    """Build one platform adapter from a tenant connector row."""
    client = http or HttpClient(float(config.get("timeout_seconds", 30)))
    providers = {
        "meta_ads": MetaAudienceProvider,
        "google_ads": GoogleAdsAudienceProvider,
        "tiktok_ads": TikTokAudienceProvider,
    }
    platform_code = normalize_platform(platform)
    if platform_code not in providers:
        raise AudienceSyncError(f"{platform_code} is a database destination, not an external provider")
    return providers[platform_code](config, credentials, client)


def _connect():
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        dbname=os.environ.get("DB_NAME", "customer360"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", "postgres"),
        port=os.environ.get("DB_PORT", "5432"),
    )


def _set_tenant_context(cursor, tenant_id: str) -> None:
    cursor.execute("SET app.tenant_id = %s", (tenant_id,))


def _load_provider_config(
    cursor, tenant_id: str, platform: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    aliases = [key for key, value in PLATFORM_ALIASES.items() if value == platform]
    cursor.execute(
        f"""
        SELECT config, credentials
        FROM {DB_SCHEMA}.crm_connector_config
        WHERE tenant_id = %(tenant_id)s
          AND connector_type = 'ADS'
          AND lower(replace(replace(provider, ' ', '_'), '-', '_')) = ANY(%(providers)s)
          AND status = 'ACTIVE'
          AND is_active
        ORDER BY is_default DESC, updated_at DESC
        LIMIT 1
        """,
        {"tenant_id": tenant_id, "providers": aliases},
    )
    row = cursor.fetchone()
    if not row:
        return None
    return dict(row.get("config") or {}), dict(row.get("credentials") or {})


def _update_run(cursor, run_id: str, status: str, **values: Any) -> None:
    fields = {"status": status, **values}
    assignments = ", ".join(f"{key} = %({key})s" for key in fields if key != "finished_at")
    if status in {"Completed", "Failed"}:
        assignments += ", finished_at = now()"
    fields["run_id"] = run_id
    cursor.execute(
        f"UPDATE {DB_SCHEMA}.crm_ad_sync_runs SET {assignments}, updated_at = now() WHERE sync_run_id = %(run_id)s",
        fields,
    )


def _sync_leo_ads(
    tenant_id: str,
    campaign_id: str,
    segment_id: str,
    audience_name: str,
    members: Iterable[AudienceMember],
    batch_size: int,
) -> ProviderResult:
    """Atomically replace one campaign's segment membership in the ads schema."""
    audience_key = f"customer360:{tenant_id}:{campaign_id}:{segment_id}"
    destination_url = os.environ.get("LEO_ADS_DATABASE_URL")
    destination = psycopg2.connect(destination_url) if destination_url else _connect()
    with closing(destination) as conn, conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"leo-ads-sync:{tenant_id}",),
            )
            cursor.execute(
                """
                SELECT m.tenant_id
                FROM leo_ads.customer360_tenant_map m
                JOIN leo_ads.tenant t ON t.tenant_id = m.tenant_id
                WHERE m.customer360_tenant_id = %s AND t.status = 'active'
                FOR KEY SHARE OF m FOR SHARE OF t
                """,
                (tenant_id,),
            )
            mapping = cursor.fetchone()
            if not mapping:
                raise AudienceSyncError("No active LEO Ads tenant mapping for Customer 360 tenant")
            ads_tenant_id = mapping["tenant_id"]
            cursor.execute(
                """
                INSERT INTO leo_ads.audience
                    (tenant_id, audience_key, name, provider, member_count_estimate,
                     definition, customer360_tenant_id)
                VALUES (%s, %s, %s, 'customer360', %s, %s::jsonb, %s)
                ON CONFLICT (tenant_id, audience_key) DO UPDATE
                SET name = EXCLUDED.name, member_count_estimate = EXCLUDED.member_count_estimate,
                    membership_version = leo_ads.audience.membership_version + 1,
                    definition = EXCLUDED.definition, updated_at = now()
                WHERE leo_ads.audience.customer360_tenant_id = EXCLUDED.customer360_tenant_id
                  AND leo_ads.audience.provider = 'customer360'
                RETURNING audience_id
                """,
                (ads_tenant_id, audience_key, audience_name, 0,
                 json.dumps({"customer360_tenant_id": tenant_id, "campaign_id": campaign_id,
                             "segment_id": segment_id}), tenant_id),
            )
            audience = cursor.fetchone()
            if not audience:
                raise AudienceSyncError("LEO Ads audience key is owned by another source")
            audience_id = audience["audience_id"]
            cursor.execute("CREATE TEMP TABLE c360_sync_members (user_key_hash bytea PRIMARY KEY, profile_ref text NOT NULL) ON COMMIT DROP")
            rows = (
                (bytes.fromhex(_hash_identifier(f"leo_ads:{tenant_id}:{member.profile_id}")), member.profile_id)
                for member in members
            )
            member_count = 0
            while batch := list(itertools.islice(rows, batch_size)):
                execute_values(cursor, "INSERT INTO c360_sync_members (user_key_hash, profile_ref) VALUES %s", batch)
                member_count += len(batch)
            cursor.execute(
                """
                INSERT INTO leo_ads.user_serving_key (user_key_hash, tenant_id, profile_ref)
                SELECT user_key_hash, %s, profile_ref FROM c360_sync_members
                ON CONFLICT (user_key_hash) DO UPDATE
                SET profile_ref = EXCLUDED.profile_ref, updated_at = now()
                WHERE leo_ads.user_serving_key.tenant_id = EXCLUDED.tenant_id
                """,
                (ads_tenant_id,),
            )
            cursor.execute("CREATE TEMP TABLE c360_removed_members (user_key_hash bytea PRIMARY KEY) ON COMMIT DROP")
            cursor.execute(
                """
                WITH removed AS (
                    DELETE FROM leo_ads.audience_member m
                    WHERE m.tenant_id = %s AND m.audience_id = %s
                      AND NOT EXISTS (SELECT 1 FROM c360_sync_members s WHERE s.user_key_hash = m.user_key_hash)
                    RETURNING m.user_key_hash
                )
                INSERT INTO c360_removed_members SELECT user_key_hash FROM removed
                """,
                (ads_tenant_id, audience_id),
            )
            cursor.execute(
                """
                INSERT INTO leo_ads.audience_member (tenant_id, audience_id, user_key_hash)
                SELECT %s, %s, user_key_hash FROM c360_sync_members
                ON CONFLICT DO NOTHING
                """,
                (ads_tenant_id, audience_id),
            )
            cursor.execute(
                """
                DELETE FROM leo_ads.user_serving_key k
                USING c360_removed_members r
                WHERE k.tenant_id = %s AND k.user_key_hash = r.user_key_hash
                  AND NOT EXISTS (
                      SELECT 1 FROM leo_ads.audience_member m
                      WHERE m.tenant_id = k.tenant_id AND m.user_key_hash = k.user_key_hash
                  )
                """,
                (ads_tenant_id,),
            )
            cursor.execute(
                "UPDATE leo_ads.audience SET member_count_estimate = %s WHERE tenant_id = %s AND audience_id = %s",
                (member_count, ads_tenant_id, audience_id),
            )
    return ProviderResult(str(audience_id), member_count)


def run_data_synch_job(
    *,
    tenant_id: str,
    campaign_id: str,
    platform: str,
    segment_id: str | None = None,
    audience_name: str | None = None,
    execution_id: str | None = None,
    dry_run: bool = False,
    batch_size: int = 1000,
    provider_factory: Callable[..., AudienceProvider] = build_provider,
) -> dict[str, Any]:
    """Synchronize one campaign segment to one external custom audience.

    The campaign and segment are re-read inside the job, so a caller cannot
    broaden the audience by sending another tenant's IDs or a different
    segment. The audit row is reused for retries, preserving the external
    audience ID and making reruns idempotent.
    """
    tenant_id = str(uuid.UUID(str(tenant_id)))
    campaign_id = str(uuid.UUID(str(campaign_id)))
    if execution_id is not None:
        execution_id = str(uuid.UUID(str(execution_id)))
    requested_segment = str(uuid.UUID(str(segment_id))) if segment_id else None
    platform_code = normalize_platform(platform)
    if not 1 <= batch_size <= 100_000:
        raise ValueError("batch_size must be between 1 and 100000")

    conn = _connect()
    run_id = None
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            _set_tenant_context(cursor, tenant_id)
            cursor.execute(
                f"""
                SELECT campaign_id, name, approval_status, status, segment_id, platform
                FROM {DB_SCHEMA}.crm_campaign
                WHERE tenant_id = %(tenant_id)s AND campaign_id = %(campaign_id)s
                """,
                {"tenant_id": tenant_id, "campaign_id": campaign_id},
            )
            campaign = cursor.fetchone()
            if not campaign:
                raise AudienceSyncError("Campaign not found for tenant")
            if campaign["approval_status"] != "Approved":
                raise AudienceSyncError("Campaign must be Approved before audience synchronization")
            selected_segment = requested_segment or str(campaign["segment_id"] or "")
            if not selected_segment or selected_segment != str(campaign["segment_id"]):
                raise AudienceSyncError("The sync segment must match the campaign audience")
            cursor.execute(
                f"""
                SELECT segment_tag, is_active
                FROM {DB_SCHEMA}.cdp_segments
                WHERE tenant_id = %(tenant_id)s AND segment_id = %(segment_id)s
                """,
                {"tenant_id": tenant_id, "segment_id": selected_segment},
            )
            segment = cursor.fetchone()
            if not segment or not segment["is_active"] or not segment["segment_tag"]:
                raise AudienceSyncError("Campaign segment is missing or inactive")
            name = (audience_name or f"{campaign['name']} - {platform_code}")[:255].strip()
            if not name:
                raise AudienceSyncError("Audience name is required")
            idempotency_key = f"{campaign_id}:{selected_segment}:{platform_code}:{name.lower()}"
            if execution_id:
                idempotency_key += f":{execution_id}"
            cursor.execute(
                "SELECT pg_advisory_lock(hashtextextended(%s, 0))",
                (f"audience-sync:leo_ads:{tenant_id}" if platform_code == "leo_ads"
                 else f"audience-sync:{tenant_id}:{campaign_id}:{platform_code}:{name.lower()}",),
            )
            cursor.execute(
                f"""
                SELECT sync_run_id, external_audience_id, attempt_count
                FROM {DB_SCHEMA}.crm_ad_sync_runs
                WHERE tenant_id = %(tenant_id)s AND idempotency_key = %(key)s
                FOR UPDATE
                """,
                {"tenant_id": tenant_id, "key": idempotency_key},
            )
            previous = cursor.fetchone()
            if previous:
                run_id = str(previous["sync_run_id"])
                external_audience_id = previous["external_audience_id"]
                attempt_count = int(previous["attempt_count"] or 0) + 1
                cursor.execute(
                    f"""
                    UPDATE {DB_SCHEMA}.crm_ad_sync_runs
                    SET status = 'Running', dry_run = %(dry_run)s,
                        attempt_count = %(attempt_count)s,
                        error_message = NULL, started_at = now(), finished_at = NULL,
                        updated_at = now()
                    WHERE sync_run_id = %(run_id)s
                    """,
                    {"run_id": run_id, "attempt_count": attempt_count, "dry_run": dry_run},
                )
            else:
                cursor.execute(
                    f"""
                    INSERT INTO {DB_SCHEMA}.crm_ad_sync_runs
                        (tenant_id, campaign_id, segment_id, platform, audience_name,
                         idempotency_key, status, dry_run)
                    VALUES (%(tenant_id)s, %(campaign_id)s, %(segment_id)s, %(platform)s,
                            %(audience_name)s, %(idempotency_key)s, 'Running', %(dry_run)s)
                    RETURNING sync_run_id
                    """,
                    {
                        "tenant_id": tenant_id,
                        "campaign_id": campaign_id,
                        "segment_id": selected_segment,
                        "platform": platform_code,
                        "audience_name": name,
                        "idempotency_key": idempotency_key,
                        "dry_run": dry_run,
                    },
                )
                inserted_run = cursor.fetchone()
                if not inserted_run:
                    raise AudienceSyncError("Could not create audience sync run")
                run_id = str(inserted_run["sync_run_id"])
                external_audience_id = None
                if execution_id:
                    cursor.execute(
                        f"""
                        SELECT external_audience_id
                        FROM {DB_SCHEMA}.crm_ad_sync_runs
                        WHERE tenant_id = %(tenant_id)s AND campaign_id = %(campaign_id)s
                          AND platform = %(platform)s AND lower(audience_name) = %(name)s
                          AND external_audience_id IS NOT NULL
                        ORDER BY updated_at DESC
                        LIMIT 1
                        """,
                        {
                            "tenant_id": tenant_id, "campaign_id": campaign_id,
                            "platform": platform_code, "name": name.lower(),
                        },
                    )
                    existing_audience = cursor.fetchone()
                    if existing_audience:
                        external_audience_id = existing_audience["external_audience_id"]
            conn.commit()

            provider_config = (
                _load_provider_config(cursor, tenant_id, platform_code)
                if not dry_run and platform_code != "leo_ads" else ({}, {})
            )
            missing_provider = provider_config is None
            if provider_config is None:
                logger.info(
                    "Audience sync provider not configured; logging only: campaign=%s platform=%s",
                    campaign_id, platform_code,
                )
                dry_run = True
                config, credentials = {}, {}
            else:
                config, credentials = provider_config
            default_country_code = config.get("default_country_code")
            cursor.execute(
                f"""
                SELECT count(*) AS matched_count
                FROM {DB_SCHEMA}.cdp_master_profiles p
                WHERE p.tenant_id = %(tenant_id)s
                  AND p.status_code = 1
                  AND %(segment_tag)s = ANY(p.segmentation_tags)
                """,
                {"tenant_id": tenant_id, "segment_tag": segment["segment_tag"]},
            )
            matched_count = int((cursor.fetchone() or {}).get("matched_count") or 0)
            if platform_code == "leo_ads":
                with conn.cursor(
                    name=f"leo_ads_{uuid.uuid4().hex}", cursor_factory=RealDictCursor
                ) as profiles:
                    profiles.itersize = batch_size
                    profiles.execute(
                        f"""
                        SELECT p.master_profile_id
                        FROM {DB_SCHEMA}.cdp_master_profiles p
                        WHERE p.tenant_id = %(tenant_id)s
                          AND p.status_code = 1
                          AND %(segment_tag)s = ANY(p.segmentation_tags)
                          AND p.communication_preferences -> 'leo_ads_opt_in' = 'true'::jsonb
                          AND NOT EXISTS (
                              SELECT 1 FROM {DB_SCHEMA}.crm_suppression_list sl
                              WHERE sl.tenant_id = p.tenant_id AND sl.status = 'ACTIVE'
                                AND (
                                    sl.master_profile_id = p.master_profile_id
                                    OR (sl.identifier_type = 'PROFILE_ID' AND sl.identifier = p.master_profile_id::text)
                                    OR (sl.identifier_type = 'EMAIL' AND lower(sl.identifier) = lower(p.email))
                                    OR (sl.identifier_type = 'PHONE' AND sl.identifier = p.phone_number)
                                )
                                AND (sl.scope = 'GLOBAL' OR sl.campaign_id = %(campaign_id)s)
                                AND (sl.expires_at IS NULL OR sl.expires_at > now())
                          )
                        """,
                        {"tenant_id": tenant_id, "segment_tag": segment["segment_tag"],
                         "campaign_id": campaign_id},
                    )
                    internal_members = (
                        AudienceMember(str(row["master_profile_id"]), None, None)
                        for row in profiles
                    )
                    if dry_run:
                        eligible_count = sum(1 for _ in internal_members)
                        result = None
                    else:
                        result = _sync_leo_ads(
                            tenant_id, campaign_id, selected_segment, name, internal_members, batch_size
                        )
                        eligible_count = result.uploaded_count
                _update_run(
                    cursor, run_id, "Completed",
                    matched_count=matched_count, eligible_count=eligible_count,
                    uploaded_count=result.uploaded_count if result else 0,
                    external_audience_id=result.audience_id if result else None,
                    dry_run=dry_run,
                    metadata=json.dumps({"reason": "dry_run"} if dry_run else {}),
                )
                conn.commit()
                response = {
                    "sync_run_id": run_id, "tenant_id": tenant_id, "campaign_id": campaign_id,
                    "segment_id": selected_segment, "platform": platform_code,
                    "matched_count": matched_count, "eligible_count": eligible_count,
                    "uploaded_count": result.uploaded_count if result else 0, "dry_run": dry_run,
                }
                if result:
                    response["external_audience_id"] = result.audience_id
                else:
                    response["reason"] = "dry_run"
                return response
            cursor.execute(
                f"""
                SELECT p.master_profile_id, p.email, p.phone_number,
                       p.communication_preferences
                FROM {DB_SCHEMA}.cdp_master_profiles p
                WHERE p.tenant_id = %(tenant_id)s
                  AND p.status_code = 1
                  AND %(segment_tag)s = ANY(p.segmentation_tags)
                  AND (p.email IS NOT NULL OR p.phone_number IS NOT NULL)
                  AND NOT EXISTS (
                      SELECT 1
                      FROM {DB_SCHEMA}.crm_suppression_list sl
                      WHERE sl.tenant_id = p.tenant_id
                        AND sl.status = 'ACTIVE'
                        AND (
                            sl.master_profile_id = p.master_profile_id
                            OR (sl.identifier_type = 'PROFILE_ID' AND sl.identifier = p.master_profile_id::text)
                            OR (sl.identifier_type = 'EMAIL' AND lower(sl.identifier) = lower(p.email))
                            OR (sl.identifier_type = 'PHONE' AND sl.identifier = p.phone_number)
                        )
                        AND (sl.scope = 'GLOBAL' OR sl.campaign_id = %(campaign_id)s)
                        AND (sl.expires_at IS NULL OR sl.expires_at > now())
                  )
                """,
                {
                    "tenant_id": tenant_id,
                    "segment_tag": segment["segment_tag"],
                    "campaign_id": campaign_id,
                },
            )
            members = []
            for row in cursor:
                if not _has_explicit_platform_consent(row, platform_code):
                    continue
                email_hash = _normalize_email(row["email"])
                phone_hash = _normalize_phone(row["phone_number"], default_country_code)
                if email_hash or phone_hash:
                    members.append(AudienceMember(str(row["master_profile_id"]), email_hash, phone_hash))
            _update_run(cursor, run_id, "Running", matched_count=matched_count, eligible_count=len(members))
            conn.commit()

        if dry_run:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                _set_tenant_context(cursor, tenant_id)
                reason = "provider_not_configured" if missing_provider else "dry_run"
                _update_run(
                    cursor, run_id, "Completed", dry_run=True,
                    metadata=json.dumps({"reason": reason}),
                )
                conn.commit()
            return {
                "sync_run_id": run_id,
                "tenant_id": tenant_id,
                "campaign_id": campaign_id,
                "segment_id": selected_segment,
                "platform": platform_code,
                "matched_count": matched_count,
                "eligible_count": len(members),
                "uploaded_count": 0,
                "dry_run": True,
                "reason": reason,
            }

        if not members:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                _set_tenant_context(cursor, tenant_id)
                _update_run(
                    cursor,
                    run_id,
                    "Completed",
                    metadata=json.dumps({"reason": "no_eligible_members"}),
                )
                conn.commit()
            return {
                "sync_run_id": run_id,
                "tenant_id": tenant_id,
                "campaign_id": campaign_id,
                "segment_id": selected_segment,
                "platform": platform_code,
                "matched_count": matched_count,
                "eligible_count": 0,
                "uploaded_count": 0,
                "dry_run": False,
            }

        provider = provider_factory(platform_code, {**config, "batch_size": batch_size}, credentials)
        result = provider.sync(name, members, external_audience_id)
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            _set_tenant_context(cursor, tenant_id)
            _update_run(
                cursor,
                run_id,
                "Completed",
                external_audience_id=result.audience_id,
                uploaded_count=result.uploaded_count,
                provider_request_id=result.request_id,
                metadata=json.dumps(result.metadata or {}),
            )
            conn.commit()
        return {
            "sync_run_id": run_id,
            "tenant_id": tenant_id,
            "campaign_id": campaign_id,
            "segment_id": selected_segment,
            "platform": platform_code,
            "matched_count": matched_count,
            "eligible_count": len(members),
            "uploaded_count": result.uploaded_count,
            "external_audience_id": result.audience_id,
            "dry_run": False,
        }
    except Exception as exc:
        conn.rollback()
        if run_id:
            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    _set_tenant_context(cursor, tenant_id)
                    _update_run(cursor, run_id, "Failed", error_message=str(exc)[:2000])
                    conn.commit()
            except Exception:
                conn.rollback()
                logger.exception("Could not mark audience-sync run %s failed", run_id)
        raise
    finally:
        conn.close()
