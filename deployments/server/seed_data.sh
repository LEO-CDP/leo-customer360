#!/usr/bin/env bash
# Run the CIR demo-data seed once against an env's database.
#   ./seed_data.sh <uat|prod>
#
# Mirrors the docker-compose `cir-demo-seed` job (dev profile): builds the
# backend-system/identity_resolution image and runs, in order:
#   scripts/init_sample_data.py   (demo tenant + ~1000 raw profiles)
#   scripts/run_demo_resolution.py(identity resolution -> master profiles)
#   scripts/seed_full_demo_data.py(full CRM / relations / events / personas)
#
# Runs on the api box by default (SEED_SERVER_KEY=api) — it has headroom; the
# backend box (Dagster) is memory-tight. All rows belong to the DEMO_TENANT_ID.
#
# RLS: the seed scripts write RLS-forced cdp_*/crm_* tables but do NOT set
# app.tenant_id themselves. On the managed (non-superuser) DB that fails the
# tenant_policy ::uuid check, so we set it for EVERY libpq connection via
# PGOPTIONS='-c app.tenant_id=<demo tenant>' (a local superuser bypasses RLS,
# which is why this is invisible in dev).
set -euo pipefail
cd "$(dirname "$0")"           # deployments/server
REPO_ROOT="$(cd ../.. && pwd)" # repo root (contains backend-system/)

ENV="${1:-}"
case "$ENV" in uat | prod) ;; *) echo "Usage: ./seed_data.sh <uat|prod>"; exit 1 ;; esac

[[ -f .env ]] && { set -a; source ./.env; set +a; }
SSH_KEY="${SSH_KEY:-$HOME/.ssh/c360-api_ed25519}"
SEED_SERVER_KEY="${SEED_SERVER_KEY:-api}"
DEMO_TENANT_ID="${DEMO_TENANT_ID:-11111111-1111-1111-1111-111111111111}"
tfval() {
  local line; line="$(grep -E "^[[:space:]]*$1[[:space:]]*=" "$2" 2>/dev/null | head -1)"
  case "$line" in
    *\"*\"*) line="${line#*\"}"; printf '%s' "${line%%\"*}" ;;
    *) line="${line#*=}"; line="${line%%#*}"; printf '%s' "$(printf '%s' "$line" | tr -d '[:space:]')" ;;
  esac
}

# --- resolve the target box from THIS deployment's outputs ---
terraform workspace select "$ENV" >/dev/null 2>&1 || { echo "ERROR: no '$ENV' server workspace — deploy the server first."; exit 1; }
SERVERS_JSON="$(terraform output -json servers 2>/dev/null || true)"
[[ -n "$SERVERS_JSON" ]] || { echo "ERROR: no servers output."; exit 1; }
srv_ip() { printf '%s' "$SERVERS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); s=d.get(sys.argv[1]) or {}; print(next((i.get(sys.argv[2]) for i in (s.get("internal_interfaces") or []) if i.get(sys.argv[2])), ""))' "$1" "$2"; }
FIP="$(srv_ip "$SEED_SERVER_KEY" floating_ip)"
[[ -n "$FIP" ]] || { echo "ERROR: no floating IP for server key '$SEED_SERVER_KEY'."; exit 1; }
BASTION="${BASTION_USER:-leocdp360}@$FIP"
SSH_OPTS=(-i "$SSH_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)

# --- DB connection from the postgres deployment ---
pg="../postgres"
DB_NAME="$(tfval db_name "$pg/overlays/$ENV.tfvars")"
DB_USER="$(tfval db_username "$pg/overlays/$ENV.tfvars")"
DB_PASS="${TF_VAR_db_password:-$(tfval db_password "$pg/terraform.tfvars")}"
DB_HOST="$( (cd "$pg" && terraform workspace select "$ENV" >/dev/null 2>&1 && terraform output -raw db_host 2>/dev/null) || true )"
DB_PORT="$( (cd "$pg" && terraform output -raw db_port 2>/dev/null) || echo 5432 )"
: "${DB_NAME:?missing db_name}"; : "${DB_USER:?missing db_username}"; : "${DB_PASS:?missing db_password}"; : "${DB_HOST:?could not read db_host from ../postgres outputs}"
# Optional: seed_full_demo_data's persona step can use GenAI if a key is present.
GENAI_KEY="${GOOGLE_GENAI_API_KEY:-}"

echo ">> Seeding CIR demo data on $BASTION (server key $SEED_SERVER_KEY)"
echo "   DB: ${DB_NAME}@${DB_HOST}:${DB_PORT} (user ${DB_USER})   tenant=${DEMO_TENANT_ID}"

# --- ship identity_resolution/ to the VM ---
echo ">> Shipping backend-system/identity_resolution/ ..."
tar -C "$REPO_ROOT/backend-system" -czf - identity_resolution \
  | ssh "${SSH_OPTS[@]}" "$BASTION" 'sudo mkdir -p /opt/c360 && sudo chown "$(id -un)" /opt/c360 && tar -C /opt/c360 -xzf -'

echo ">> Building the CIR image and running the seed (a few minutes on a small box) ..."
PW_B64="$(printf %s "$DB_PASS" | base64 | tr -d '\n')"
GK_B64="$(printf %s "$GENAI_KEY" | base64 | tr -d '\n')"
ssh "${SSH_OPTS[@]}" "$BASTION" 'bash -s' "$DB_HOST" "$DB_PORT" "$DB_NAME" "$DB_USER" "$PW_B64" "$DEMO_TENANT_ID" "$GK_B64" <<'REMOTE'
set -euo pipefail
DB_HOST="$1"; DB_PORT="$2"; DB_NAME="$3"; DB_USER="$4"; DB_PW="$(printf %s "$5" | base64 -d)"; TENANT="$6"; GK="$(printf %s "${7:-}" | base64 -d 2>/dev/null || true)"
if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io
  sudo systemctl enable --now docker
fi
# docker.io has no buildx -> strip the BuildKit `RUN --mount` (pip-cache only).
sed -i 's/ --mount=[^ ]*//g' /opt/c360/identity_resolution/Dockerfile
sudo docker build -t customer360-cir /opt/c360/identity_resolution
# One-shot seed. PGOPTIONS sets app.tenant_id for every connection so RLS-forced
# writes for the demo tenant succeed under the non-superuser role. DB_SCHEMA=DB_NAME
# (the app convention; the schema is named the same as the database).
sudo docker run --rm --network host \
  -e DB_HOST="$DB_HOST" -e DB_PORT="$DB_PORT" -e DB_NAME="$DB_NAME" -e DB_USER="$DB_USER" \
  -e DB_PASSWORD="$DB_PW" -e DB_SCHEMA="$DB_NAME" \
  -e PGOPTIONS="-c app.tenant_id=$TENANT" \
  ${GK:+-e GOOGLE_GENAI_API_KEY="$GK"} \
  customer360-cir sh -c '
    set -e
    echo "== init_sample_data =="   && python scripts/init_sample_data.py &&
    echo "== run_demo_resolution ==" && python scripts/run_demo_resolution.py &&
    echo "== seed_full_demo_data ==" && python scripts/seed_full_demo_data.py'
echo "   CIR seed finished."
REMOTE
echo ">> Done. Verify: rows for the demo tenant in cdp_master_profiles / cdp_raw_profiles_stage."
