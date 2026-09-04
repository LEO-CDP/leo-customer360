# Data Source Type 2: Data Connector API (Server-side Pull/Sync)

## 1. Overview & Architectural Role

Data Source Type 2 represents scheduled, server-to-server automated pull connectors. Background workers authenticate against external vendor REST/GraphQL APIs, pull incremental analytics, conversion, and lead records, and normalize them into the Customer 360 database.

```mermaid
flowchart LR
    subgraph External["External Cloud APIs"]
        GA4["Google Analytics 4 Data API"]
        META["Meta Graph / Lead Ads API"]
        TT["TikTok Marketing API"]
        ZALO["Zalo OA v3.0 API"]
    end

    subgraph SyncService["backend-system / data_synch"]
        ORCH["Dagster Scheduler / data_synch_job"]
        WORKER["API Connector Worker"]
        REDIS["Redis Rate Limits & Cursors"]
    end

    subgraph Staging["Database Staging"]
        STAGE_PRF["cdp_raw_profiles_stage"]
        STAGE_EVT["cdp_raw_events"]
    end

    ORCH --> WORKER
    WORKER --> REDIS
    WORKER -->|"Pull batch with OAuth / Tokens"| External
    External -->|"JSON response records"| WORKER
    WORKER -->|"Flatten & Upsert"| STAGE_PRF
    WORKER -->|"Normalize Events"| STAGE_EVT
```

---

## 2. Configuration & Credential Schema

Credentials, OAuth secrets, and target endpoints are stored encrypted in `sys_data_source.access_tokens` JSONB.

### Configuration Fields in `sys_data_source`
- `data_source_url`: Base API URL of the vendor service (e.g. `https://analyticsdata.googleapis.com`).
- `data_source_hosts`: Allowed API hosts (e.g. `["analyticsdata.googleapis.com", "oauth2.googleapis.com"]`).
- `access_tokens`: Encrypted JSON object storing tokens, client IDs, and secret keys.

### Example Connector Configurations

#### Google Analytics 4 (Data API)
```json
{
  "property_id": "312984920",
  "client_email": "c360-ga4-sync@myproject.iam.gserviceaccount.com",
  "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQC...\n-----END PRIVATE KEY-----\n",
  "metrics": ["eventCount", "totalRevenue", "engagementRate"],
  "dimensions": ["eventName", "sessionSource", "sessionMedium", "campaignName"]
}
```

#### Meta Graph API / Lead Ads
```json
{
  "access_token": "EAAQ...",
  "page_id": "104928192849",
  "app_id": "928374829102",
  "app_secret": "sec_...",
  "leadgen_form_ids": ["1029384756"]
}
```

#### Zalo Official Account API (v3.0)
```json
{
  "app_id": "182736491827",
  "secret_key": "zalo_sec_...",
  "oa_id": "2837492817293",
  "refresh_token": "zalo_refresh_token_..."
}
```

---

## 3. Ingestion & Transformation Pipeline

1. **Cursor & Incremental Checkpoint**:
   - Connector workers query the maximum `last_activity_at` or `stat_time_day` from `cdp_raw_events` for the specific `data_source_id`.
   - Redis stores watermark checkpoints (`source_state:{data_source_id}`) preventing duplicate pulls.
2. **Batch Pull with Backoff**:
   - Requests are throttled using token-bucket rate limiters in Redis to adhere to vendor API quotas.
   - Transient failures (HTTP 429, 503) invoke exponential backoff with jitter.
3. **Normalization & Mapping**:
   - Vendor records are flattened and mapped to standard staging models:
     - Form responses $\rightarrow$ `cdp_raw_profiles_stage` (`field_email`, `field_phone`, `first_name`, `last_name`).
     - Behavioral facts $\rightarrow$ `cdp_raw_events` (`event_name`, `event_time`, `event_data`).

---

## 4. Orchestration & Execution

Connector jobs are scheduled and monitored through Dagster in `backend-system/data_synch/`:
- **Job Name**: `data_synch_job`
- **Execution Interval**: Configurable per source (default: Hourly at `0 * * * *` or Daily at `0 2 * * *`).
- **Telemetry & Monitoring**: Dagster run status is queried and logged; failures report directly to administrator alerts.
