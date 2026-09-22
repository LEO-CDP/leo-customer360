#!/usr/bin/env bash
# Reusable local E2E integration test:
#   production-shaped events -> tracking API -> MinIO RAW JSONL.GZ ->
#   Dagster analytics -> raw analytics -> CIR -> master analytics/persona/API
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"

if [[ -f "$ENV_FILE" ]]; then
	set -a
	# shellcheck disable=SC1090
	source "$ENV_FILE"
	set +a
fi

require_command() {
	command -v "$1" >/dev/null 2>&1 || {
		echo "ERROR: required command not found: $1" >&2
		exit 1
	}
}

for command_name in curl jq docker python3; do
	require_command "$command_name"
done

TRACKING_API_URL="${TRACKING_API_URL:-http://localhost:8010/api/v1/tracking/logs}"
CUSTOMER360_API_URL="${CUSTOMER360_API_URL:-http://localhost:8008/api/v1}"
DATA_SOURCE_ID="${TRACKING_DATA_SOURCE_ID:-15dc39d4-ae42-5c60-9c77-66f05dcae448}"
TENANT_ID="${CUSTOMER360_TENANT_ID:-11111111-1111-1111-1111-111111111111}"
MINIO_CONTAINER="${MINIO_CONTAINER:-customer360-minio}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-customer360-postgres}"
MINIO_ROOT_USER="${MINIO_ROOT_USER:?Set MINIO_ROOT_USER in .env}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:?Set MINIO_ROOT_PASSWORD in .env}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${DB_NAME:-customer360}"
DB_SCHEMA="${DB_SCHEMA:-customer360}"
MASTER_PROFILE_S3_BUCKET="${MASTER_PROFILE_S3_BUCKET:-c360-master-profiles}"
S3_WAIT_SECONDS="${E2E_S3_WAIT_SECONDS:-5}"
ANALYTICS_POLL_SECONDS="${E2E_ANALYTICS_POLL_SECONDS:-5}"
ANALYTICS_TIMEOUT_SECONDS="${E2E_ANALYTICS_TIMEOUT_SECONDS:-300}"
CIR_DIR="$ROOT_DIR/backend-system/identity_resolution"
CIR_PYTHON="${CIR_PYTHON:-$CIR_DIR/.venv/bin/python}"
CIR_POLL_INTERVAL_SECONDS="${CIR_POLL_INTERVAL_SECONDS:-600}"
E2E_APPLY_LOCAL_MIGRATIONS="${E2E_APPLY_LOCAL_MIGRATIONS:-true}"
CUSTOMER360_API_TOKEN="${CUSTOMER360_API_TOKEN:-${LEO_API_TOKEN:-}}"
CUSTOMER360_USERNAME="${CUSTOMER360_USERNAME:-${DEFAULT_ROOT_USERNAME:-}}"
CUSTOMER360_PASSWORD="${CUSTOMER360_PASSWORD:-${DEFAULT_ROOT_PASSWORD:-}}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

if ! [[ "$S3_WAIT_SECONDS" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
	echo "ERROR: E2E_S3_WAIT_SECONDS must be a non-negative number" >&2
	exit 1
fi
if ! [[ "$ANALYTICS_POLL_SECONDS" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
	echo "ERROR: E2E_ANALYTICS_POLL_SECONDS must be a non-negative number" >&2
	exit 1
fi
if ! [[ "$ANALYTICS_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
	echo "ERROR: E2E_ANALYTICS_TIMEOUT_SECONDS must be a positive integer" >&2
	exit 1
fi

new_uuid() {
	python3 -c 'import uuid; print(uuid.uuid4())'
}

SESSION_ID="${E2E_SESSION_ID:-$(new_uuid)}"
ANONYMOUS_ID="${E2E_ANONYMOUS_ID:-$(new_uuid | tr -d '-')}"
DEVICE_FINGERPRINT="${E2E_DEVICE_FINGERPRINT:-$(new_uuid | tr -d '-')}"
PAGE_EVENT_ID="${E2E_PAGE_EVENT_ID:-$(new_uuid)}"
CLICK_EVENT_ID="${E2E_CLICK_EVENT_ID:-$(new_uuid)}"
EVENT_TIME="$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)"
CLICK_TIME="$(date -u -d '+1 second' +%Y-%m-%dT%H:%M:%S.%3NZ 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%S.%3NZ)"

log() {
	printf '[e2e] %s\n' "$*"
}

fail() {
	printf '[e2e] ERROR: %s\n' "$*" >&2
	exit 1
}

if [[ "$E2E_APPLY_LOCAL_MIGRATIONS" == "true" ]]; then
	MIGRATION_FILE="$ROOT_DIR/customer360-database/migrations/009_data_source_analytics.sql"
	[[ -f "$MIGRATION_FILE" ]] || fail "missing analytics migration: $MIGRATION_FILE"
	log "Applying idempotent local analytics schema migration"
	docker exec -i "$POSTGRES_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" \
		-v ON_ERROR_STOP=1 < "$MIGRATION_FILE" >/dev/null || \
		fail "could not apply $MIGRATION_FILE"
fi

[[ -x "$CIR_PYTHON" ]] || fail "identity-resolution virtualenv is missing: $CIR_PYTHON"
CIR_SENSOR_INTERVAL="$(
	cd "$CIR_DIR"
	CIR_POLL_INTERVAL_SECONDS="$CIR_POLL_INTERVAL_SECONDS" "$CIR_PYTHON" -c \
		'import dagster_defs; print(dagster_defs.POLL_INTERVAL_SECONDS)'
)" || fail "could not load identity-resolution Dagster definition"
[[ "$CIR_SENSOR_INTERVAL" == "$CIR_POLL_INTERVAL_SECONDS" ]] || \
	fail "identity-resolution sensor interval is $CIR_SENSOR_INTERVAL; expected $CIR_POLL_INTERVAL_SECONDS"
log "Identity-resolution sensor interval verified: ${CIR_SENSOR_INTERVAL}s"

post_json() {
	local url="$1"
	local body="$2"
	curl --fail-with-body --silent --show-error --max-time 30 \
		-H 'Accept: application/json' \
		-H 'Content-Type: application/json' \
		-H 'User-Agent: c360-local-e2e-template/1.0' \
		-X POST "$url" \
		--data "$body"
}

log "Creating production-shaped anonymous Web SDK events"
EVENTS_JSON="$(jq -cn \
	--arg page_event_id "$PAGE_EVENT_ID" \
	--arg click_event_id "$CLICK_EVENT_ID" \
	--arg event_time "$EVENT_TIME" \
	--arg click_time "$CLICK_TIME" \
	--arg anonymous_id "$ANONYMOUS_ID" \
	--arg session_id "$SESSION_ID" \
	--arg device_fingerprint "$DEVICE_FINGERPRINT" \
	'[
		{"event_id":$page_event_id,"event_name":"page-view","event_time":$event_time,"page_url":"https%3A%2F%2Fwww.bigdatavietnam.org%2F2012%2F12%2Fdata-science-starter-kit.html","page_title":"Big%20Data%20Vietnam%3A%20Data%20Science","referrer_url":"https://www.bigdatavietnam.org/2012/12/about-mc2ads-project.html","anonymous_id":$anonymous_id,"session_id":$session_id,"device_fingerprint":$device_fingerprint,"event_data":{},"device_type":"desktop"},
		{"event_id":$click_event_id,"event_name":"click","event_time":$click_time,"page_url":"https%3A%2F%2Fwww.bigdatavietnam.org%2F2012%2F12%2Fdata-science-starter-kit.html","page_title":"Big%20Data%20Vietnam%3A%20Data%20Science","referrer_url":"https://www.bigdatavietnam.org/2012/12/about-mc2ads-project.html","anonymous_id":$anonymous_id,"session_id":$session_id,"device_fingerprint":$device_fingerprint,"event_data":{"target":"article-link","label":"starter-kit"},"device_type":"desktop"}
	]')"

REQUEST_BODY="$(jq -cn \
	--arg data_source_id "$DATA_SOURCE_ID" \
	--arg session_id "$SESSION_ID" \
	--arg anonymous_id "$ANONYMOUS_ID" \
	--arg device_fingerprint "$DEVICE_FINGERPRINT" \
	--argjson events "$EVENTS_JSON" \
	'{data_source_id:$data_source_id,session_id:$session_id,anonymous_id:$anonymous_id,device_fingerprint:$device_fingerprint,events:$events}')"

log "POST $TRACKING_API_URL"
TRACKING_RESPONSE="$(post_json "$TRACKING_API_URL" "$REQUEST_BODY")" || fail "tracking API rejected the event batch"
BUCKET="$(jq -er '.bucket' <<<"$TRACKING_RESPONSE")" || fail "tracking response did not contain bucket"
OBJECT_KEY="$(jq -er '.object_key' <<<"$TRACKING_RESPONSE")" || fail "tracking response did not contain object_key"
EVENT_COUNT="$(jq -er '.event_count' <<<"$TRACKING_RESPONSE")" || fail "tracking response did not contain event_count"
EXPECTED_COUNT="$(jq 'length' <<<"$EVENTS_JSON")"
[[ "$EVENT_COUNT" == "$EXPECTED_COUNT" ]] || fail "tracking API stored $EVENT_COUNT events; expected $EXPECTED_COUNT"
log "Tracking API accepted $EVENT_COUNT events in s3://$BUCKET/$OBJECT_KEY"

log "Waiting ${S3_WAIT_SECONDS}s for MinIO visibility"
sleep "$S3_WAIT_SECONDS"
MINIO_OBJECT="local/$BUCKET/$OBJECT_KEY"
STORED_NDJSON=""
docker exec "$MINIO_CONTAINER" mc alias set local http://127.0.0.1:9000 \
	"$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1 || \
	fail "could not configure the MinIO mc alias"
for attempt in 1 2 3; do
	if STORED_NDJSON="$(docker exec "$MINIO_CONTAINER" mc cat "$MINIO_OBJECT" 2>/dev/null | gzip -dc)"; then
		break
	fi
	if [[ "$attempt" == 3 ]]; then
		fail "could not read $MINIO_OBJECT from MinIO container"
	fi
	sleep 2
done

STORED_JSON="$(jq -s '.' <<<"$STORED_NDJSON")"
STORED_COUNT="$(jq 'length' <<<"$STORED_JSON")"
[[ "$STORED_COUNT" == "$EXPECTED_COUNT" ]] || \
	fail "MinIO stored $STORED_COUNT events; expected $EXPECTED_COUNT"
for ((index = 0; index < EXPECTED_COUNT; index++)); do
	stored_event="$(jq -c ".[$index].payload" <<<"$STORED_JSON")"
	schema_version="$(jq -r ".[$index].schema_version // empty" <<<"$STORED_JSON")"
	event_id="$(jq -r ".[$index].event_id // empty" <<<"$STORED_JSON")"
	event_time="$(jq -r ".[$index].event_time // empty" <<<"$STORED_JSON")"
	[[ "$schema_version" == "1" && -n "$event_id" && -n "$event_time" ]] || fail "MinIO record $((index + 1)) has invalid canonical envelope"
	stored_source="$(jq -r ".[$index].data_source_id // empty" <<<"$STORED_JSON")"
	[[ "$stored_source" == "$DATA_SOURCE_ID" ]] || \
		fail "MinIO record $((index + 1)) has data_source_id=$stored_source"
	stored_event_name="$(jq -r ".[$index].payload.event_name // empty" <<<"$STORED_JSON")"
	stored_anonymous_id="$(jq -r ".[$index].payload.anonymous_id // empty" <<<"$STORED_JSON")"
	stored_fingerprint="$(jq -r ".[$index].payload.device_fingerprint // empty" <<<"$STORED_JSON")"
	[[ "$stored_event_name" == "page-view" || "$stored_event_name" == "click" ]] || \
		fail "MinIO record $((index + 1)) has unexpected event_name=$stored_event_name"
	[[ "$stored_anonymous_id" == "$ANONYMOUS_ID" ]] || \
		fail "MinIO record $((index + 1)) lost anonymous_id"
	[[ "$stored_fingerprint" == "$DEVICE_FINGERPRINT" ]] || \
		fail "MinIO record $((index + 1)) lost device_fingerprint"
	done
log "MinIO verification passed for $EXPECTED_COUNT stored events"
printf '%s\n' "$STORED_JSON" | jq .

ANALYTICS_TOKEN="$CUSTOMER360_API_TOKEN"
if [[ -z "$ANALYTICS_TOKEN" ]]; then
	[[ -n "$CUSTOMER360_USERNAME" && -n "$CUSTOMER360_PASSWORD" ]] || \
		fail "set CUSTOMER360_API_TOKEN or CUSTOMER360_USERNAME/CUSTOMER360_PASSWORD"
	LOGIN_BODY="$(jq -cn --arg username "$CUSTOMER360_USERNAME" --arg password "$CUSTOMER360_PASSWORD" --arg tenant_id "$TENANT_ID" '{username:$username,password:$password,tenant_id:$tenant_id}')"
	LOGIN_RESPONSE="$(post_json "$CUSTOMER360_API_URL/auth/login" "$LOGIN_BODY")" || fail "Customer 360 login failed"
	ANALYTICS_TOKEN="$(jq -er '.access_token' <<<"$LOGIN_RESPONSE")" || fail "Customer 360 login returned no access_token"
fi

AUTH_HEADER="Authorization: Bearer $ANALYTICS_TOKEN"
log "Triggering analytics through Customer 360 API"
TRIGGER_BODY="$TMP_DIR/analytics-trigger.json"
TRIGGER_STATUS="$(curl --silent --show-error --max-time 30 \
	-H 'Accept: application/json' -H "$AUTH_HEADER" \
	-o "$TRIGGER_BODY" -w '%{http_code}' \
	-X POST "$CUSTOMER360_API_URL/analytics/source-analytics/process")" || \
	fail "analytics trigger request failed"

if [[ "$TRIGGER_STATUS" == "409" ]]; then
	ACTIVE_STATUS="$(curl --fail-with-body --silent --show-error --max-time 30 \
		-H 'Accept: application/json' -H "$AUTH_HEADER" \
		"$CUSTOMER360_API_URL/analytics/source-analytics/status")" || \
		fail "analytics trigger returned 409 and status lookup failed"
	RUN_ID="$(jq -er '.active_submission.run_id // empty' <<<"$ACTIVE_STATUS")" || true
	[[ -n "$RUN_ID" ]] || fail "analytics trigger returned 409 with no active run_id: $(cat "$TRIGGER_BODY")"
	ACTIVE_RUN_STATUS_RESPONSE="$(curl --fail-with-body --silent --show-error --max-time 30 \
		-H 'Accept: application/json' -H "$AUTH_HEADER" \
		"$CUSTOMER360_API_URL/analytics/source-analytics/status/$RUN_ID")" || \
		fail "could not inspect active analytics run $RUN_ID"
	ACTIVE_RUN_STATUS="$(jq -r '.status // empty' <<<"$ACTIVE_RUN_STATUS_RESPONSE")"
	if [[ "$ACTIVE_RUN_STATUS" == "success" || "$ACTIVE_RUN_STATUS" == "failure" ]]; then
		log "Analytics submission was stale ($ACTIVE_RUN_STATUS); lease cleared, submitting a fresh run"
		TRIGGER_STATUS="$(curl --silent --show-error --max-time 30 \
			-H 'Accept: application/json' -H "$AUTH_HEADER" \
			-o "$TRIGGER_BODY" -w '%{http_code}' \
			-X POST "$CUSTOMER360_API_URL/analytics/source-analytics/process")" || \
			fail "fresh analytics trigger request failed"
		[[ "$TRIGGER_STATUS" =~ ^2[0-9][0-9]$ ]] || \
			fail "fresh analytics trigger returned HTTP $TRIGGER_STATUS: $(cat "$TRIGGER_BODY")"
		TRIGGER_RESPONSE="$(cat "$TRIGGER_BODY")"
		RUN_ID="$(jq -er '.run_id' <<<"$TRIGGER_RESPONSE")" || fail "fresh analytics trigger returned no run_id"
	else
		log "Analytics run already active; reusing run_id: $RUN_ID"
	fi
elif [[ "$TRIGGER_STATUS" =~ ^2[0-9][0-9]$ ]]; then
	TRIGGER_RESPONSE="$(cat "$TRIGGER_BODY")"
	RUN_ID="$(jq -er '.run_id' <<<"$TRIGGER_RESPONSE")" || fail "analytics trigger returned no run_id"
	log "Analytics run submitted: $RUN_ID"
else
	fail "analytics trigger returned HTTP $TRIGGER_STATUS: $(cat "$TRIGGER_BODY")"
fi

STARTED_AT="$(date +%s)"
while true; do
	STATUS_RESPONSE="$(curl --fail-with-body --silent --show-error --max-time 30 -H 'Accept: application/json' -H "$AUTH_HEADER" "$CUSTOMER360_API_URL/analytics/source-analytics/status/$RUN_ID")" || fail "analytics status request failed"
	RUN_STATUS="$(jq -r '.status // empty' <<<"$STATUS_RESPONSE")"
	case "$RUN_STATUS" in
		success) break ;;
		failure) fail "analytics run failed: $(jq -c . <<<"$STATUS_RESPONSE")" ;;
		running|submitted) : ;;
		*) fail "unexpected analytics status: $RUN_STATUS" ;;
	esac
	if (( $(date +%s) - STARTED_AT >= ANALYTICS_TIMEOUT_SECONDS )); then
		fail "analytics run timed out after ${ANALYTICS_TIMEOUT_SECONDS}s: $(jq -c . <<<"$STATUS_RESPONSE")"
	fi
	sleep "$ANALYTICS_POLL_SECONDS"
done
log "Analytics run completed successfully"

RAW_SQL="SELECT json_build_object('raw_profile_id',raw_profile_id,'status_code',status_code,'external_customer_id',external_customer_id,'data_source_analytics',data_source_analytics)::text FROM ${DB_SCHEMA}.cdp_raw_profiles_stage WHERE tenant_id='${TENANT_ID}' AND data_source_id='${DATA_SOURCE_ID}' AND external_customer_id='${ANONYMOUS_ID}' ORDER BY created_at DESC LIMIT 1;"
RAW_PROFILE_JSON="$(docker exec "$POSTGRES_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tA -v ON_ERROR_STOP=1 -c "$RAW_SQL")" || fail "could not query raw profile analytics"
[[ -n "$RAW_PROFILE_JSON" ]] || fail "analytics did not stage the anonymous raw profile"
jq -e --arg data_source_id "$DATA_SOURCE_ID" \
	'.external_customer_id != null and .data_source_analytics[$data_source_id].total_tracked_events >= 2 and .data_source_analytics[$data_source_id].page_views >= 1 and .data_source_analytics[$data_source_id].clicks >= 1' \
	<<<"$RAW_PROFILE_JSON" >/dev/null || fail "raw profile data_source_analytics was not computed correctly"
log "Raw profile analytics verification passed"
printf '%s\n' "$RAW_PROFILE_JSON" | jq .

log "Running backend-system/identity_resolution drain (sensor interval=${CIR_POLL_INTERVAL_SECONDS}s)"
CIR_RESULT="$(
	cd "$CIR_DIR"
	CIR_POLL_INTERVAL_SECONDS="$CIR_POLL_INTERVAL_SECONDS" "$CIR_PYTHON" -c \
		'from identity_resolution.cir_tasks import run_identity_resolution_tasks; print(run_identity_resolution_tasks())'
)" || fail "identity resolution daily drain failed"
log "Identity resolution processed $(tail -n 1 <<<"$CIR_RESULT") raw profile batch result"

MASTER_SQL="SELECT json_build_object('master_profile_id',mp.master_profile_id,'domain',mp.domain,'persona_name',mp.persona_name,'data_source_analytics',mp.data_source_analytics,'linked_raw_profile_count',mp.linked_raw_profile_count)::text FROM ${DB_SCHEMA}.cdp_master_profiles mp JOIN ${DB_SCHEMA}.cdp_profile_links link ON link.tenant_id=mp.tenant_id AND link.master_profile_id=mp.master_profile_id AND link.status='ACTIVE' JOIN ${DB_SCHEMA}.cdp_raw_profiles_stage raw ON raw.tenant_id=link.tenant_id AND raw.raw_profile_id=link.raw_profile_id WHERE mp.tenant_id='${TENANT_ID}' AND raw.data_source_id='${DATA_SOURCE_ID}' AND raw.external_customer_id='${ANONYMOUS_ID}' ORDER BY mp.updated_at DESC LIMIT 1;"
MASTER_PROFILE_JSON="$(docker exec "$POSTGRES_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tA -v ON_ERROR_STOP=1 -c "$MASTER_SQL")" || fail "could not query resolved master profile"
[[ -n "$MASTER_PROFILE_JSON" ]] || fail "identity resolution did not create/link a master profile"
jq -e --arg data_source_id "$DATA_SOURCE_ID" \
	'.persona_name == "Web Visitor" and .data_source_analytics[$data_source_id].total_tracked_events >= 2 and .data_source_analytics[$data_source_id].page_views >= 1 and .data_source_analytics[$data_source_id].clicks >= 1' \
	<<<"$MASTER_PROFILE_JSON" >/dev/null || fail "master profile persona or merged data_source_analytics is incorrect"
MASTER_PROFILE_ID="$(jq -er '.master_profile_id' <<<"$MASTER_PROFILE_JSON")"
log "Master profile Web Visitor and analytics merge verification passed: $MASTER_PROFILE_ID"
printf '%s\n' "$MASTER_PROFILE_JSON" | jq .

MASTER_OBJECT="local/${MASTER_PROFILE_S3_BUCKET}/${MASTER_PROFILE_ID}.json"
MASTER_EVENT_JSON="$(docker exec "$MINIO_CONTAINER" mc cat "$MASTER_OBJECT" 2>/dev/null)" || \
	fail "could not read master profile event projection $MASTER_OBJECT"
jq -e --arg master_profile_id "$MASTER_PROFILE_ID" \
	'length >= 2 and .[0].event_time >= .[1].event_time and all(.[]; .master_profile_id == $master_profile_id or .master_profile_id == null)' \
	<<<"$MASTER_EVENT_JSON" >/dev/null || fail "master profile event projection is incomplete or not newest-first"
log "Master profile S3 JSON projection verification passed"

FROM_EVENT_TIME="$(jq -rn --arg value "$CLICK_TIME" '$value|@uri')"
TO_EVENT_TIME="$(jq -rn --arg value "$EVENT_TIME" '$value|@uri')"
TIMELINE_RESPONSE="$(curl --fail-with-body --silent --show-error --max-time 30 \
	-H 'Accept: application/json' -H "$AUTH_HEADER" \
	"$CUSTOMER360_API_URL/master-profiles/$MASTER_PROFILE_ID/timeline?limit=8&from_event_time=$FROM_EVENT_TIME&to_event_time=$TO_EVENT_TIME")" || \
	fail "master profile timeline API lookup failed"
jq -e 'length >= 2' <<<"$TIMELINE_RESPONSE" >/dev/null || \
	fail "master profile timeline API returned fewer than two projected events"
log "Master profile timeline API date-range verification passed"

PROFILE_RESPONSE="$(curl --fail-with-body --silent --show-error --max-time 30 \
	-H 'Accept: application/json' -H "$AUTH_HEADER" \
	"$CUSTOMER360_API_URL/master-profiles/$MASTER_PROFILE_ID")" || fail "master profile API lookup failed"
jq -e --arg data_source_id "$DATA_SOURCE_ID" \
	'.persona_name == "Web Visitor" and .data_source_analytics[$data_source_id].total_tracked_events >= 2' \
	<<<"$PROFILE_RESPONSE" >/dev/null || fail "Customer 360 API did not expose merged profile analytics"
log "Customer 360 API master profile verification passed"

SUMMARY_RESPONSE="$(curl --fail-with-body --silent --show-error --max-time 30 -H 'Accept: application/json' -H "$AUTH_HEADER" "$CUSTOMER360_API_URL/metadata/data-sources/$DATA_SOURCE_ID")" || fail "data-source API lookup failed"
SQL="SELECT json_build_object('data_source_id',data_source_id,'total_tracked_event',total_tracked_event,'avg_daily_event',avg_daily_event,'avg_events_per_profile',avg_events_per_profile)::text FROM ${DB_SCHEMA}.sys_data_source WHERE tenant_id='${TENANT_ID}' AND data_source_id='${DATA_SOURCE_ID}' AND status=1;"
DB_SUMMARY="$(docker exec "$POSTGRES_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tA -v ON_ERROR_STOP=1 -c "$SQL")" || fail "could not query PostgreSQL data-source summary"
[[ -n "$DB_SUMMARY" ]] || fail "data source was not found in PostgreSQL"

jq -e --arg data_source_id "$DATA_SOURCE_ID" '.data_source_id==$data_source_id and (.total_tracked_event|tonumber)>0 and (.avg_daily_event|tonumber)>0 and (.avg_events_per_profile|tonumber)>0' <<<"$SUMMARY_RESPONSE" >/dev/null || fail "Customer 360 API summary is not positive or belongs to another source"
jq -e --arg data_source_id "$DATA_SOURCE_ID" '.data_source_id==$data_source_id and (.total_tracked_event|tonumber)>0 and (.avg_daily_event|tonumber)>0 and (.avg_events_per_profile|tonumber)>0' <<<"$DB_SUMMARY" >/dev/null || fail "PostgreSQL summary is not positive or belongs to another source"

log "PostgreSQL verification passed"
printf '%s\n' "$DB_SUMMARY" | jq .
log "E2E integration test passed"
