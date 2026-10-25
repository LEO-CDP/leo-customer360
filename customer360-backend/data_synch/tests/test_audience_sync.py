import uuid

import pytest

import audience_sync


TENANT_ID = str(uuid.uuid4())
CAMPAIGN_ID = str(uuid.uuid4())
SEGMENT_ID = str(uuid.uuid4())
RUN_ID = str(uuid.uuid4())
PROFILE_ID = str(uuid.uuid4())


class FakeCursor:
    def __init__(self):
        self.result = None
        self.rows = []
        self.statements = []
        self.profile_preferences = {"meta_ads_opt_in": True}

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.statements.append((sql, params or {}))
        lowered = sql.lower()
        if "from customer360.crm_campaign " in lowered:
            self.result = {
                "campaign_id": CAMPAIGN_ID,
                "name": "Spring retention",
                "approval_status": "Approved",
                "status": "Running",
                "segment_id": SEGMENT_ID,
                "platform": "meta_ads",
            }
            self.rows = []
        elif "from customer360.cdp_segments " in lowered:
            self.result = {"segment_tag": "spring", "is_active": True}
            self.rows = []
        elif "from customer360.crm_ad_sync_runs " in lowered:
            self.result = None
            self.rows = []
        elif "insert into customer360.crm_ad_sync_runs" in lowered:
            self.result = {"sync_run_id": RUN_ID}
            self.rows = []
        elif "from customer360.crm_connector_config" in lowered:
            self.result = {
                "config": {"ad_account_id": "act-1"},
                "credentials": {"access_token": "secret"},
            }
            self.rows = []
        elif "select count(*) as matched_count" in lowered:
            self.result = {"matched_count": 1}
            self.rows = []
        elif "from customer360.cdp_master_profiles" in lowered:
            self.result = None
            self.rows = [
                {
                    "master_profile_id": PROFILE_ID,
                    "email": "Person@example.com",
                    "phone_number": "+84901234567",
                    "communication_preferences": self.profile_preferences,
                }
            ]
        else:
            self.result = None
            self.rows = []

    def fetchone(self):
        value = self.result
        self.result = None
        return value

    def __iter__(self):
        rows = self.rows
        self.rows = []
        return iter(rows)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, **_kwargs):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


class FakeProvider:
    def __init__(self):
        self.calls = []

    def sync(self, audience_name, members, external_audience_id=None):
        self.calls.append((audience_name, members, external_audience_id))
        return audience_sync.ProviderResult("external-1", len(members), "request-1")


def test_platform_aliases_are_normalized():
    assert audience_sync.normalize_platform("Facebook") == "meta_ads"
    assert audience_sync.normalize_platform("Google Ads") == "google_ads"
    assert audience_sync.normalize_platform("tiktok_ads") == "tiktok_ads"
    with pytest.raises(audience_sync.AudienceSyncError):
        audience_sync.normalize_platform("unknown")


def test_identifiers_are_normalized_and_hashed():
    assert audience_sync._normalize_email(" Person@Example.COM ") == audience_sync._hash_identifier(
        "person@example.com"
    )
    assert audience_sync._normalize_phone("+84 (901) 234-567") == audience_sync._hash_identifier("+84901234567")
    assert audience_sync._normalize_phone("0901234567") is None


@pytest.mark.parametrize(
    "preferences, expected",
    [
        ({"meta_ads_opt_in": True}, True),
        ({"meta_ads_opt_in": False}, False),
        ({"meta_ads_opt_in": "true"}, False),
        ({"google_ads_opt_in": True}, False),
        ({}, False),
        (None, False),
    ],
)
def test_provider_consent_requires_literal_true_for_exact_platform(
    preferences, expected
):
    assert audience_sync._has_explicit_platform_consent(
        {"communication_preferences": preferences},
        "meta_ads",
    ) is expected


def test_missing_provider_consent_prevents_audience_upload(monkeypatch):
    connection = FakeConnection()
    connection.cursor_instance.profile_preferences = {}
    provider = FakeProvider()
    monkeypatch.setattr(audience_sync, "_connect", lambda: connection)

    result = audience_sync.run_data_synch_job(
        tenant_id=TENANT_ID,
        campaign_id=CAMPAIGN_ID,
        platform="meta_ads",
        segment_id=SEGMENT_ID,
        provider_factory=lambda *_args: provider,
    )

    assert result["matched_count"] == 1
    assert result["eligible_count"] == 0
    assert result["uploaded_count"] == 0
    assert provider.calls == []


def test_run_data_synch_job_hashes_profiles_and_returns_provider_result(monkeypatch):
    connection = FakeConnection()
    provider = FakeProvider()
    monkeypatch.setattr(audience_sync, "_connect", lambda: connection)

    result = audience_sync.run_data_synch_job(
        tenant_id=TENANT_ID,
        campaign_id=CAMPAIGN_ID,
        platform="Facebook",
        segment_id=SEGMENT_ID,
        provider_factory=lambda *_args: provider,
    )

    assert result["external_audience_id"] == "external-1"
    assert result["uploaded_count"] == 1
    assert provider.calls[0][0] == "Spring retention - meta_ads"
    member = provider.calls[0][1][0]
    assert member.email_hash == audience_sync._hash_identifier("person@example.com")
    assert member.phone_hash == audience_sync._hash_identifier("+84901234567")
    assert "Person@example.com" not in repr(provider.calls)
    assert connection.commits >= 3


def test_dry_run_does_not_load_connector_or_call_provider(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(audience_sync, "_connect", lambda: connection)
    provider_called = False

    def provider_factory(*_args):
        nonlocal provider_called
        provider_called = True
        raise AssertionError("provider must not be called for dry-run")

    result = audience_sync.run_data_synch_job(
        tenant_id=TENANT_ID,
        campaign_id=CAMPAIGN_ID,
        platform="meta_ads",
        segment_id=SEGMENT_ID,
        dry_run=True,
        provider_factory=provider_factory,
    )

    assert result["dry_run"] is True
    assert result["uploaded_count"] == 0
    assert provider_called is False


def test_missing_ads_connector_logs_dry_run_without_calling_provider(monkeypatch, caplog):
    connection = FakeConnection()
    monkeypatch.setattr(audience_sync, "_connect", lambda: connection)
    monkeypatch.setattr(audience_sync, "_load_provider_config", lambda *_: None)
    provider = FakeProvider()

    with caplog.at_level("INFO"):
        result = audience_sync.run_data_synch_job(
            tenant_id=TENANT_ID, campaign_id=CAMPAIGN_ID, platform="meta_ads",
            provider_factory=lambda *_: provider,
        )

    assert result["dry_run"] is True
    assert result["reason"] == "provider_not_configured"
    assert result["uploaded_count"] == 0
    assert provider.calls == []
    assert "not configured" in caplog.text
    assert "Person@example.com" not in caplog.text
    assert any(
        params.get("metadata") == '{"reason": "provider_not_configured"}'
        for _, params in connection.cursor_instance.statements
        if isinstance(params, dict)
    )


def test_campaign_replays_use_separate_audience_audit_keys(monkeypatch):
    keys = []
    for execution_id in (str(uuid.uuid4()), str(uuid.uuid4())):
        connection = FakeConnection()
        monkeypatch.setattr(audience_sync, "_connect", lambda: connection)
        audience_sync.run_data_synch_job(
            tenant_id=TENANT_ID, campaign_id=CAMPAIGN_ID, platform="meta_ads",
            execution_id=execution_id, dry_run=True,
        )
        inserts = [
            params for statement, params in connection.cursor_instance.statements
            if "INSERT INTO customer360.crm_ad_sync_runs" in statement
        ]
        assert len(inserts) == 1
        assert inserts[0]["idempotency_key"].endswith(f":{execution_id}")
        keys.append(inserts[0]["idempotency_key"])
    assert keys[0] != keys[1]


def test_replay_reuses_existing_external_audience_without_overwriting_prior_run(monkeypatch):
    class ExistingAudienceCursor(FakeCursor):
        def execute(self, statement, params=None):
            super().execute(statement, params)
            if "AND external_audience_id IS NOT NULL" in str(statement):
                self.result = {"external_audience_id": "existing-audience"}

    connection = FakeConnection()
    connection.cursor_instance = ExistingAudienceCursor()
    provider = FakeProvider()
    monkeypatch.setattr(audience_sync, "_connect", lambda: connection)
    result = audience_sync.run_data_synch_job(
        tenant_id=TENANT_ID, campaign_id=CAMPAIGN_ID, platform="meta_ads",
        execution_id=str(uuid.uuid4()), provider_factory=lambda *_: provider,
    )
    assert result["uploaded_count"] == 1
    assert provider.calls[0][2] == "existing-audience"
    inserts = [
        sql for sql, _ in connection.cursor_instance.statements
        if "INSERT INTO customer360.crm_ad_sync_runs" in sql
    ]
    assert len(inserts) == 1
