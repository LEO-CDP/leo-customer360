#!/usr/bin/env bash
# Deploy Redis for customer360-api.
#
#   uat  -> a Redis DOCKER container ON the api server VM (co-located, --network host,
#           :<redis_port>). No managed service, no Terraform. Cheapest option.
#   prod -> a managed VNG MemStore (Redis) instance (package db.s-general-2x4) via Terraform.
#
# Usage:
#   ./deploy.sh uat                       # (re)deploy the uat Redis container
#   ./deploy.sh uat destroy               # remove the uat Redis container
#   ./deploy.sh prod [plan|apply|destroy] # manage the prod managed MemStore (default plan)
#
# The Redis password (shared with customer360-api's REDIS_PASSWORD) comes from
# terraform.tfvars (redis_password = "...") or .env (TF_VAR_redis_password).
# After deploying, (re)deploy the api so it picks up REDIS_*: ../server/deploy-api.sh <env>
set -euo pipefail
cd "$(dirname "$0")"

ENV="${1:-}"
ACTION="${2:-}"
case "$ENV" in
  uat | prod) ;;
  *) echo "Usage: ./deploy.sh <uat|prod> [plan|apply|destroy]"; exit 1 ;;
esac

[[ -f .env ]] && { set -a; source ./.env; set +a; }
# Read a tfvars value: content between quotes for strings (keeps '#'), or the bare
# token with any trailing comment stripped for unquoted numbers/bools (e.g. redis_port).
tfval() {
  local line; line="$(grep -E "^[[:space:]]*$1[[:space:]]*=" "$2" 2>/dev/null | head -1)"
  case "$line" in
    *\"*\"*) line="${line#*\"}"; printf '%s' "${line%%\"*}" ;;
    *) line="${line#*=}"; line="${line%%#*}"; printf '%s' "$(printf '%s' "$line" | tr -d '[:space:]')" ;;
  esac
}

# Redis password: shared with the api. From .env (TF_VAR_redis_password) or terraform.tfvars.
REDIS_PASSWORD="${TF_VAR_redis_password:-$(tfval redis_password terraform.tfvars)}"
: "${REDIS_PASSWORD:?set redis_password in terraform.tfvars (or TF_VAR_redis_password in .env)}"

if [[ "$ENV" == "uat" ]]; then
  # ------------------------------------------------------------------ UAT: Docker
  ovl="overlays/uat.tfvars"
  REDIS_PORT="$(tfval redis_port "$ovl")"; REDIS_PORT="${REDIS_PORT:-6580}"
  REDIS_IMAGE="$(tfval redis_image "$ovl")"; REDIS_IMAGE="${REDIS_IMAGE:-customer360-redis:local}"
  REDIS_BUILD_CTX="$(tfval redis_build_context "$ovl")" # repo ./redis (built on the box), or empty to pull
  API_SERVER_KEY="${API_SERVER_KEY:-$(tfval api_server_key "$ovl")}"; API_SERVER_KEY="${API_SERVER_KEY:-api}"
  SSH_KEY="${SSH_KEY:-$HOME/.ssh/c360-api_ed25519}"

  # Discover the api box's public (floating) IP from the sibling ../server outputs.
  SERVERS_JSON="$( (cd ../server && terraform workspace select uat >/dev/null 2>&1 && terraform output -json servers 2>/dev/null) || true )"
  [[ -n "$SERVERS_JSON" ]] || { echo "ERROR: no ../server servers output — deploy the server first."; exit 1; }
  FIP="$(printf '%s' "$SERVERS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); s=d.get(sys.argv[1]) or {}; print(next((i.get("floating_ip") for i in (s.get("internal_interfaces") or []) if i.get("floating_ip")), ""))' "$API_SERVER_KEY")"
  [[ -n "$FIP" ]] || { echo "ERROR: no floating IP for server key '$API_SERVER_KEY'."; exit 1; }
  BASTION="${BASTION_USER:-leocdp360}@$FIP"
  SSH_OPTS=(-i "$SSH_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)

  if [[ "$ACTION" == "destroy" ]]; then
    echo ">> Removing Redis container on $BASTION ..."
    ssh "${SSH_OPTS[@]}" "$BASTION" 'sudo docker rm -f c360-redis >/dev/null 2>&1; echo "   removed (data volume c360-redis-data kept)"'
    exit 0
  fi

  # Build from the repo ./redis on the box (preferred — carries the cache-tuned
  # redis.conf), else pull a plain image. Ship the context first when building.
  BUILD=0
  if [[ -n "$REDIS_BUILD_CTX" && -d "$REDIS_BUILD_CTX" ]]; then
    BUILD=1
    echo ">> Shipping build context $REDIS_BUILD_CTX -> $BASTION:/opt/c360/redis ..."
    tar -C "$(dirname "$REDIS_BUILD_CTX")" -czf - "$(basename "$REDIS_BUILD_CTX")" \
      | ssh "${SSH_OPTS[@]}" "$BASTION" 'sudo mkdir -p /opt/c360 && sudo chown "$(id -un)" /opt/c360 && tar -C /opt/c360 -xzf -'
  fi

  echo ">> uat: deploying Redis ($REDIS_IMAGE, build=$BUILD) on $BASTION at 127.0.0.1:$REDIS_PORT ..."
  PW_B64="$(printf %s "$REDIS_PASSWORD" | base64 | tr -d '\n')"
  ssh "${SSH_OPTS[@]}" "$BASTION" 'bash -s' "$REDIS_IMAGE" "$REDIS_PORT" "$PW_B64" "$BUILD" <<'REMOTE'
set -euo pipefail
IMG="$1"; PORT="$2"; PW="$(printf %s "$3" | base64 -d)"; BUILD="$4"
if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io
  sudo systemctl enable --now docker
fi
if [[ "$BUILD" == "1" ]]; then
  # Classic builder (docker.io has no buildx); the ./redis Dockerfile uses no
  # BuildKit features, so DOCKER_BUILDKIT=0 just skips fetching the syntax frontend.
  sudo DOCKER_BUILDKIT=0 docker build -t "$IMG" /opt/c360/redis
else
  sudo docker pull "$IMG" >/dev/null
fi
sudo docker rm -f c360-redis >/dev/null 2>&1 || true
# --network host: reachable at 127.0.0.1:$PORT by the api container (also --network host).
# Named volume persists the append-only file across restarts.
if [[ "$BUILD" == "1" ]]; then
  # Image ENTRYPOINT runs redis-server with the baked redis.conf (port 6580,
  # appendonly, maxmemory 256mb allkeys-lru); append --requirepass like compose does.
  # REDIS_PASSWORD env is also needed by the image's HEALTHCHECK (redis-cli -a "$REDIS_PASSWORD").
  sudo docker run -d --name c360-redis --restart unless-stopped --network host \
    -e REDIS_PASSWORD="$PW" -v c360-redis-data:/data "$IMG" --requirepass "$PW"
else
  sudo docker run -d --name c360-redis --restart unless-stopped --network host \
    -v c360-redis-data:/data "$IMG" redis-server --port "$PORT" --requirepass "$PW" --appendonly yes
fi
sleep 2
sudo docker exec c360-redis redis-cli -p "$PORT" -a "$PW" --no-auth-warning ping
sudo docker ps --filter name=c360-redis --format '   running: {{.Names}} ({{.Status}})'
REMOTE
  echo ">> Done. Now (re)deploy the api so it uses the cache: ../server/deploy-api.sh uat"
  exit 0
fi

# ------------------------------------------------------------------ PROD: Terraform
ACTION="${ACTION:-plan}"
VAR_FILE="overlays/prod.tfvars"
[[ -f "$VAR_FILE" ]] || { echo "ERROR: overlay $VAR_FILE not found."; exit 1; }
if [[ ! -f .env && ! -f terraform.tfvars ]]; then
  echo "ERROR: no credentials. cp .env.example .env (or terraform.tfvars.example terraform.tfvars) and fill it in."; exit 1
fi

terraform init -input=false
terraform workspace select prod 2>/dev/null || terraform workspace new prod
case "$ACTION" in
  plan)    terraform plan    -input=false -var-file="$VAR_FILE" ;;
  apply)   terraform apply   -input=false -auto-approve -var-file="$VAR_FILE" ;;
  destroy) terraform destroy -input=false -var-file="$VAR_FILE" ;;
  *) echo "Unknown action: $ACTION (use plan | apply | destroy)"; exit 1 ;;
esac
