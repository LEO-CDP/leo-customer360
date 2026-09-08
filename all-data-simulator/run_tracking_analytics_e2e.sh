#!/usr/bin/env bash
# Reusable local E2E template:
#   create events -> tracking API -> MinIO JSONL -> Dagster analytics -> PostgreSQL
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

for command_name in curl jq docker; do
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
S3_WAIT_SECONDS="${E2E_S3_WAIT_SECONDS:-5}"
ANALYTICS_POLL_SECONDS="${E2E_ANALYTICS_POLL_SECONDS:-5}"
ANALYTICS_TIMEOUT_SECONDS="${E2E_ANALYTICS_TIMEOUT_SECONDS:-300}"
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

SESSION_ID="${E2E_SESSION_ID:-e2e-session-$(date -u +%Y%m%dT%H%M%SZ)-${RANDOM}}"
USER_ID="${E2E_USER_ID:-e2e-user-${RANDOM}}"
ORDER_ID="${E2E_ORDER_ID:-e2e-order-${RANDOM}}"

log() {
	printf '[e2e] %s\n' "$*"
}

fail() {
	printf '[e2e] ERROR: %s\n' "$*" >&2
	exit 1
}

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

log "Creating deterministic ecommerce events for user $USER_ID"
EVENTS_JSON="$(jq -cn \
	--arg order_id "$ORDER_ID" \
	'[
		{"event_name":"ad_impression","page_url":"https://shop.example.test/ads/ad-001","source":"local-e2e-template","ad_id":"ad-001","campaign":"summer_audio_sale","product_id":"product-001"},
		{"event_name":"product_view","page_url":"https://shop.example.test/products/product-001","source":"local-e2e-template","product_id":"product-001"},
		{"event_name":"price_request","page_url":"https://shop.example.test/products/product-001","source":"local-e2e-template","product_id":"product-001","answer_price_vnd":1490000},
		{"event_name":"purchase","page_url":"https://shop.example.test/checkout/complete","source":"local-e2e-template","product_id":"product-001","quantity":1,"total_price_vnd":1490000,"currency":"VND","order_id":$order_id}
	]')"

REQUEST_BODY="$(jq -cn \
	--arg data_source_id "$DATA_SOURCE_ID" \
	--arg session_id "$SESSION_ID" \
	--arg user_id "$USER_ID" \
	--argjson events "$EVENTS_JSON" \
	'{data_source_id:$data_source_id,session_id:$session_id,user_id:$user_id,events:$events}')"

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
	if STORED_NDJSON="$(docker exec "$MINIO_CONTAINER" mc cat "$MINIO_OBJECT" 2>/dev/null)"; then
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
	expected_event="$(jq -c ".[$index] + {session_id: \"$SESSION_ID\", user_id: \"$USER_ID\"}" <<<"$EVENTS_JSON")"
	stored_event="$(jq -c ".[$index].event" <<<"$STORED_JSON")"
	stored_source="$(jq -r ".[$index].data_source_id // empty" <<<"$STORED_JSON")"
	[[ "$stored_source" == "$DATA_SOURCE_ID" ]] || \
		fail "MinIO record $((index + 1)) has data_source_id=$stored_source"
	[[ "$stored_event" == "$expected_event" ]] || \
		fail "MinIO record $((index + 1)) does not match the submitted event"
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
	log "Analytics run already active; reusing run_id: $RUN_ID"
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

SUMMARY_RESPONSE="$(curl --fail-with-body --silent --show-error --max-time 30 -H 'Accept: application/json' -H "$AUTH_HEADER" "$CUSTOMER360_API_URL/metadata/data-sources/$DATA_SOURCE_ID")" || fail "data-source API lookup failed"
SQL="SELECT json_build_object('data_source_id',data_source_id,'total_tracked_event',total_tracked_event,'avg_daily_event',avg_daily_event,'avg_events_per_profile',avg_events_per_profile)::text FROM ${DB_SCHEMA}.sys_data_source WHERE tenant_id='${TENANT_ID}' AND data_source_id='${DATA_SOURCE_ID}' AND status=1;"
DB_SUMMARY="$(docker exec "$POSTGRES_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tA -v ON_ERROR_STOP=1 -c "$SQL")" || fail "could not query PostgreSQL data-source summary"
[[ -n "$DB_SUMMARY" ]] || fail "data source was not found in PostgreSQL"

jq -e --arg data_source_id "$DATA_SOURCE_ID" '.data_source_id==$data_source_id and (.total_tracked_event|tonumber)>0 and (.avg_daily_event|tonumber)>0 and (.avg_events_per_profile|tonumber)>0' <<<"$SUMMARY_RESPONSE" >/dev/null || fail "Customer 360 API summary is not positive or belongs to another source"
jq -e --arg data_source_id "$DATA_SOURCE_ID" '.data_source_id==$data_source_id and (.total_tracked_event|tonumber)>0 and (.avg_daily_event|tonumber)>0 and (.avg_events_per_profile|tonumber)>0' <<<"$DB_SUMMARY" >/dev/null || fail "PostgreSQL summary is not positive or belongs to another source"

log "PostgreSQL verification passed"
printf '%s\n' "$DB_SUMMARY" | jq .
log "E2E integration test passed"
