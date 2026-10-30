#!/usr/bin/env bash
# Deploy customer360-backend (the Dagster orchestrator, :3000) onto the GreenNode VKS
# Kubernetes cluster as a singleton Deployment.
#   ./deploy-backend-vks.sh <uat|prod> [deploy|destroy]
#
# Replaces the retired vServer model (one docker container on the jobs box). Same inputs, read from the SAME Terraform state that script used:
#   * Postgres (../postgres): the shared managed DB + the dedicated `dagster` metadata DB.
#     REQUIRED — the image's renderer refuses to start without it.
#   * vStorage (../storage): compute logs bucket + the S3 key. Required when
#     dagster_require_s3 = "true" (the default in overlays/).
#   * Redis (../cache, on the api box): the analytics/dedup cache. The vServer backend ran its
#     own LOCAL redis on the jobs box; pods cannot, so this uses the api box's PRIVATE redis.
#     Its keys are NOT migrated — dedup state starts empty.
#   * SMTP (../server/smtp.<env>.env): email_engine dispatch; absent -> adapter stays mock.
#
# Re-runnable. Requires kubectl + a kubeconfig for the VKS cluster. Overrides:
#   KUBECONFIG            path to the kubeconfig (default ./kubeconfig-vks-<env>.yaml, then
#                         ../kubeconfig-vks-<env>.yaml, ../server/…, ~/.kube/vks-<env>.yaml)
#   KUBE_CONTEXT          context inside that kubeconfig (default: its current one)
#   BACKEND_NAMESPACE     (default customer360 — shared with event-api and promotions)
#   DB_HOST / REDIS_HOST  skip the terraform-output lookup (e.g. when terraform is not installed)
#   GHCR_PULL_TOKEN       long-lived PAT (read:packages) for the imagePullSecret; not needed
#                         when the shared `ghcr-pull` secret already exists in the namespace
#   IMAGE_TAG             GHCR tag (default: image_tag in overlays/<env>.tfvars, else latest)
set -euo pipefail
cd "$(dirname "$0")" # deployments/customer360-backend
MANIFESTS="k8s"
ROOT="$(cd .. && pwd)"

ENV="${1:-}"
case "$ENV" in uat | prod) ;; *) echo "Usage: ./deploy-backend-vks.sh <uat|prod> [deploy|destroy]"; exit 1 ;; esac
ACTION="${2:-deploy}"
case "$ACTION" in deploy | destroy) ;; *) echo "Usage: ./deploy-backend-vks.sh <uat|prod> [deploy|destroy]"; exit 1 ;; esac

[[ -f .env ]] && { set -a; source ./.env; set +a; }
NAMESPACE="${BACKEND_NAMESPACE:-customer360}"
OVERLAY="$MANIFESTS/overlays/$ENV"
[[ -d "$OVERLAY" ]] || { echo "ERROR: no overlay for '$ENV' at $OVERLAY."; exit 1; }

# --- kubeconfig: explicit only, so we can never apply into whatever context is current ---
command -v kubectl >/dev/null 2>&1 || { echo "ERROR: kubectl not found on PATH."; exit 1; }
if [[ -z "${KUBECONFIG:-}" ]]; then
  for c in "./kubeconfig-vks-$ENV.yaml" "../kubeconfig-vks-$ENV.yaml" "../server/kubeconfig-vks-$ENV.yaml" "$HOME/.kube/vks-$ENV.yaml"; do
    [[ -f "$c" ]] && { KUBECONFIG="$c"; break; }
  done
fi
[[ -n "${KUBECONFIG:-}" && -f "$KUBECONFIG" ]] || {
  echo "ERROR: no VKS kubeconfig. Download it from https://vks.console.greennode.ai (cluster -> Kubeconfig)"
  echo "       and save it as ./kubeconfig-vks-$ENV.yaml, or set KUBECONFIG=/path/to/it."
  exit 1
}
export KUBECONFIG
KUBECTL=(kubectl)
[[ -n "${KUBE_CONTEXT:-}" ]] && KUBECTL+=(--context "$KUBE_CONTEXT")
"${KUBECTL[@]}" version -o json >/dev/null 2>&1 || { echo "ERROR: cannot reach the cluster with KUBECONFIG=$KUBECONFIG."; exit 1; }
"${KUBECTL[@]}" auth can-i create deployments -n "$NAMESPACE" >/dev/null 2>&1 || {
  echo "ERROR: this kubeconfig cannot create Deployments in namespace '$NAMESPACE'."; exit 1; }
echo ">> Cluster: $("${KUBECTL[@]}" config current-context)  (namespace $NAMESPACE)"

# --- teardown: label-scoped, namespace and the shared ghcr-pull secret are kept ---
if [[ "$ACTION" == "destroy" ]]; then
  echo ">> Deleting customer360-backend from namespace $NAMESPACE ..."
  "${KUBECTL[@]}" -n "$NAMESPACE" delete deployment,service,configmap,secret \
    -l app.kubernetes.io/name=customer360-backend --ignore-not-found
  echo ">> Done. Namespace kept (shared). The Dagster metadata DB 'dagster' and the S3 logs are NOT dropped."
  exit 0
fi

. "$ROOT/lib/tfvars.sh" # tfval + srv_ip
# Refuse to deploy where no node is at least as big as the vServer box Dagster runs on today.
[[ "${SKIP_CAPACITY_CHECK:-0}" == "1" ]] || ./check-capacity.sh "$ENV" || {
  echo "ERROR: capacity check failed (override with SKIP_CAPACITY_CHECK=1 at your own risk)."; exit 1; }
ovl="overlays/$ENV.tfvars"
[[ -f "$ovl" ]] || { echo "ERROR: overlay $ovl not found."; exit 1; }
DAGSTER_PORT="$(tfval dagster_port "$ovl")"; DAGSTER_PORT="${DAGSTER_PORT:-3000}"
DAGSTER_PG_DB="$(tfval dagster_pg_db "$ovl")"; DAGSTER_PG_DB="${DAGSTER_PG_DB:-dagster}"
MAX_RUNS="$(tfval dagster_max_concurrent_runs "$ovl")"; MAX_RUNS="${MAX_RUNS:-10}"
REQUIRE_S3="$(tfval dagster_require_s3 "$ovl")"; REQUIRE_S3="${REQUIRE_S3:-true}"

# --- Postgres ---
pg="../postgres"
DB_NAME="$(tfval db_name "$pg/overlays/$ENV.tfvars")"
DB_USER="$(tfval db_username "$pg/overlays/$ENV.tfvars")"
DB_PASS="${TF_VAR_db_password:-$(tfval db_password "$pg/terraform.tfvars")}"
DB_HOST="${DB_HOST:-$( (cd "$pg" && terraform workspace select "$ENV" >/dev/null 2>&1 && terraform output -raw db_host 2>/dev/null) || true )}"
DB_PORT="$( (cd "$pg" && terraform output -raw db_port 2>/dev/null) || echo 5432 )"
: "${DB_NAME:?missing db_name in $pg/overlays/$ENV.tfvars}"
: "${DB_USER:?missing db_username in $pg/overlays/$ENV.tfvars}"
: "${DB_PASS:?missing db_password (set TF_VAR_db_password or $pg/terraform.tfvars)}"
: "${DB_HOST:?could not read db_host from $pg outputs — set DB_HOST or deploy the database first}"
echo ">> Postgres: ${DB_NAME}@${DB_HOST}:${DB_PORT} (user $DB_USER); Dagster metadata DB '$DAGSTER_PG_DB'"

# --- vStorage (compute logs + data buckets) ---
store="../storage"
. "$ROOT/lib/s3.sh" 2>/dev/null || true
S3_ENDPOINT="$(tfval s3_endpoint "$store/overlays/$ENV.tfvars")"
S3_REGION="$(tfval region "$store/overlays/$ENV.tfvars")"; S3_REGION="${S3_REGION:-us-east-1}"
S3_BUCKET="$(tfval bucket_names "$store/overlays/$ENV.tfvars")" # first quoted bucket name
S3_ACCESS_KEY="${TF_VAR_access_key:-$(tfval access_key "$store/terraform.tfvars")}"
S3_SECRET_KEY="${TF_VAR_secret_key:-$(tfval secret_key "$store/terraform.tfvars")}"
S3_AUTO_CREATE="${S3_AUTO_CREATE_BUCKETS:-$(tfval s3_auto_create_buckets "$store/overlays/$ENV.tfvars")}"; S3_AUTO_CREATE="${S3_AUTO_CREATE:-true}"
MASTER_PROFILE_S3_BUCKET="${MASTER_PROFILE_S3_BUCKET:-$(tfval master_profile_s3_bucket "$store/overlays/$ENV.tfvars")}"; MASTER_PROFILE_S3_BUCKET="${MASTER_PROFILE_S3_BUCKET:-c360-master-profiles}"
PRODUCT_IMPORT_S3_BUCKET="${PRODUCT_IMPORT_S3_BUCKET:-$(tfval product_import_s3_bucket "$store/overlays/$ENV.tfvars")}"; PRODUCT_IMPORT_S3_BUCKET="${PRODUCT_IMPORT_S3_BUCKET:-c360-product-imports}"
CONTENT_IMPORT_S3_BUCKET="${CONTENT_IMPORT_S3_BUCKET:-$(tfval content_import_s3_bucket "$store/overlays/$ENV.tfvars")}"; CONTENT_IMPORT_S3_BUCKET="${CONTENT_IMPORT_S3_BUCKET:-c360-content-imports}"
if [[ -z "$S3_ENDPOINT" || -z "$S3_BUCKET" || -z "$S3_ACCESS_KEY" || -z "$S3_SECRET_KEY" ]]; then
  [[ "$REQUIRE_S3" == "true" ]] && { echo "ERROR: vStorage not fully configured (endpoint/bucket/key) and dagster_require_s3 = true."; exit 1; }
  echo ">> S3: not fully configured — compute logs will use local storage"
else
  echo ">> S3: $S3_ENDPOINT bucket=$S3_BUCKET (path-style) — compute logs -> vStorage"
fi

# --- Redis: the api box's private redis (see header) ---
SERVERS_JSON="$( (cd ../server && terraform workspace select "$ENV" >/dev/null 2>&1 && terraform output -json servers 2>/dev/null) || true )"
REDIS_HOST="${REDIS_HOST:-$([[ -n "$SERVERS_JSON" ]] && srv_ip "${REDIS_SERVER_KEY:-api}" fixed_ip)}"
REDIS_PORT="$(tfval redis_port ../cache/overlays/$ENV.tfvars)"; REDIS_PORT="${REDIS_PORT:-6580}"
REDIS_PASS="${TF_VAR_redis_password:-$(tfval redis_password ../cache/terraform.tfvars)}"
: "${REDIS_HOST:?no Redis host — set REDIS_HOST or deploy ../server first}"
echo ">> Redis: $REDIS_HOST:$REDIS_PORT"

# --- image: the CI-built GHCR image ---
. "$ROOT/lib/ghcr.sh"
SERVICE="customer360-dagster"
IMAGE="$(image_ref "$SERVICE" "$(resolve_tag "overlays/$ENV.tfvars")")"
echo ">> Image: $IMAGE"
GHCR_USER="${GHCR_USER:-${GITHUB_ACTOR:-token}}"
GHCR_TOKEN="${GHCR_PULL_TOKEN:-${GHCR_TOKEN:-${GITHUB_TOKEN:-}}}"

# --- render the env overlay (gitignored: it holds credentials) ---
GEN="$MANIFESTS/generated/$ENV"
rm -rf "$GEN"; mkdir -p "$GEN"
umask 077
{
  echo "DB_HOST=$DB_HOST"; echo "DB_PORT=$DB_PORT"; echo "DB_NAME=$DB_NAME"; echo "DB_USER=$DB_USER"; echo "DB_SCHEMA=$DB_NAME"
  echo "DAGSTER_PG_DB=$DAGSTER_PG_DB"
  echo "DAGSTER_MAX_CONCURRENT_RUNS=$MAX_RUNS"
  echo "DAGSTER_REQUIRE_S3=$REQUIRE_S3"
  echo "S3_ENDPOINT=$S3_ENDPOINT"; echo "S3_ENDPOINT_URL=$S3_ENDPOINT"; echo "S3_REGION=$S3_REGION"; echo "S3_FORCE_PATH_STYLE=true"
  echo "MINIO_BUCKET=$S3_BUCKET"; echo "S3_AUTO_CREATE_BUCKETS=$S3_AUTO_CREATE"
  echo "MASTER_PROFILE_S3_BUCKET=$MASTER_PROFILE_S3_BUCKET"
  echo "PRODUCT_IMPORT_S3_BUCKET=$PRODUCT_IMPORT_S3_BUCKET"
  echo "CONTENT_IMPORT_S3_BUCKET=$CONTENT_IMPORT_S3_BUCKET"
  echo "REDIS_HOST=$REDIS_HOST"; echo "REDIS_PORT=$REDIS_PORT"; echo "REDIS_DB=0"
  echo "DOCS_EMBEDDING_PROVIDER=${DOCS_EMBEDDING_PROVIDER:-openai}"
  echo "DOCS_OPENAI_EMBEDDING_MODEL=${DOCS_OPENAI_EMBEDDING_MODEL:-text-embedding-3-small}"
  echo "DOCS_OPENAI_EMBEDDING_DIMENSIONS=${DOCS_OPENAI_EMBEDDING_DIMENSIONS:-384}"
  if [[ -f "../server/smtp.$ENV.env" ]]; then
    ( set -a; . "../server/smtp.$ENV.env"; [[ -f "../server/smtp.$ENV.local.env" ]] && . "../server/smtp.$ENV.local.env"
      echo "EMAIL_DISPATCH_ADAPTER=${EMAIL_DISPATCH_ADAPTER:-smtp}"; echo "SMTP_HOST=${SMTP_HOST:-}"; echo "SMTP_PORT=${SMTP_PORT:-587}"
      echo "SMTP_USERNAME=${SMTP_USERNAME:-}"; echo "SMTP_USE_TLS=${SMTP_USE_TLS:-true}"
      echo "EMAIL_FROM_ADDRESS=${EMAIL_FROM_ADDRESS:-}"; echo "EMAIL_FROM_NAME=${EMAIL_FROM_NAME:-LEO CDP}" )
    echo ">> Email: SMTP dispatch enabled for $ENV" >&2
  else
    echo "EMAIL_DISPATCH_ADAPTER=mock"; echo ">> Email: dispatch = mock (no ../server/smtp.$ENV.env)" >&2
  fi
} > "$GEN/config.env"
{
  echo "DB_PASSWORD=$DB_PASS"
  echo "AWS_ACCESS_KEY_ID=$S3_ACCESS_KEY"; echo "AWS_SECRET_ACCESS_KEY=$S3_SECRET_KEY"
  echo "S3_ACCESS_KEY_ID=$S3_ACCESS_KEY"; echo "S3_SECRET_ACCESS_KEY=$S3_SECRET_KEY"
  echo "MINIO_ROOT_USER=$S3_ACCESS_KEY"; echo "MINIO_ROOT_PASSWORD=$S3_SECRET_KEY"
  [[ -n "$REDIS_PASS" ]] && echo "REDIS_PASSWORD=$REDIS_PASS"
  echo "DOCS_OPENAI_API_KEY=${DOCS_OPENAI_API_KEY:-}"; echo "DOCS_GEMINI_API_KEY=${DOCS_GEMINI_API_KEY:-}"
  if [[ -f "../server/smtp.$ENV.env" ]]; then
    ( set -a; . "../server/smtp.$ENV.env"; [[ -f "../server/smtp.$ENV.local.env" ]] && . "../server/smtp.$ENV.local.env"
      echo "SMTP_PASSWORD=${BREVO_SMTP_PASSWORD:-${SMTP_PASSWORD:-}}" )
  fi
} > "$GEN/secret.env"

if [[ "$IMAGE" == *@* ]]; then IMG_LINE="    newName: ${IMAGE%@*}
    digest: ${IMAGE#*@}"; else IMG_LINE="    newName: ${IMAGE%:*}
    newTag: ${IMAGE##*:}"; fi
cat > "$GEN/kustomization.yaml" <<KUSTOMIZE
# GENERATED by deploy-backend-vks.sh — do not edit; re-run the script instead.
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: $NAMESPACE
resources:
  - ../../overlays/$ENV
images:
  - name: ghcr.io/leo-cdp/leo-customer360/customer360-dagster
$IMG_LINE
# Hash-suffixed names are deliberate: a config/secret change renames the object, which rolls the pod.
configMapGenerator:
  - name: dagster-config
    envs: [config.env]
secretGenerator:
  - name: dagster-secrets
    envs: [secret.env]
KUSTOMIZE

# --- namespace + the shared GHCR pull secret (created by whichever service deploys first) ---
"${KUBECTL[@]}" create namespace "$NAMESPACE" --dry-run=client -o yaml | "${KUBECTL[@]}" apply -f - >/dev/null
if [[ -n "$GHCR_TOKEN" ]]; then
  "${KUBECTL[@]}" -n "$NAMESPACE" create secret docker-registry ghcr-pull \
    --docker-server=ghcr.io --docker-username="$GHCR_USER" --docker-password="$GHCR_TOKEN" \
    --dry-run=client -o yaml | "${KUBECTL[@]}" apply -f - >/dev/null
  echo ">> GHCR pull secret refreshed."
else
  "${KUBECTL[@]}" -n "$NAMESPACE" get secret ghcr-pull >/dev/null 2>&1 || {
    echo "ERROR: no GHCR_PULL_TOKEN and no existing 'ghcr-pull' secret in $NAMESPACE."; exit 1; }
  echo ">> GHCR pull secret: reusing the existing one."
fi

# --- ensure the dedicated Dagster metadata DB. A managed-PG app user usually cannot
#     CREATE DATABASE, which is the renderer's best-effort path, so do it here too (idempotent). ---
echo ">> Ensuring database '$DAGSTER_PG_DB' on $DB_HOST ..."
"${KUBECTL[@]}" -n "$NAMESPACE" delete pod dagster-db-init --ignore-not-found >/dev/null
"${KUBECTL[@]}" -n "$NAMESPACE" run dagster-db-init --rm -i --restart=Never --pod-running-timeout=5m --image=postgres:16-alpine \
  --env="PGPASSWORD=$DB_PASS" --command -- sh -c \
  "psql -h '$DB_HOST' -p '$DB_PORT' -U '$DB_USER' -d postgres -tAc \"SELECT 1 FROM pg_database WHERE datname='$DAGSTER_PG_DB'\" | grep -q 1 || psql -h '$DB_HOST' -p '$DB_PORT' -U '$DB_USER' -d postgres -c 'CREATE DATABASE \"$DAGSTER_PG_DB\"'" \
  || echo "   WARNING: could not ensure '$DAGSTER_PG_DB' — pre-create it if the pod crash-loops on Postgres."

echo ">> Applying manifests (overlay: $ENV) ..."
"${KUBECTL[@]}" apply -k "$GEN"

echo ">> Waiting for the rollout ..."
"${KUBECTL[@]}" -n "$NAMESPACE" rollout status deploy/dagster --timeout=12m || {
  echo "   ROLLOUT FAILED. Likely causes: Postgres login/DB (see the [render-instance] lines), vStorage key,"
  echo "   or OOMKilled on the small UAT nodes. Logs:"
  "${KUBECTL[@]}" -n "$NAMESPACE" logs deploy/dagster --tail=40 || true
  "${KUBECTL[@]}" -n "$NAMESPACE" get pods -l app=dagster || true
  exit 1
}
"${KUBECTL[@]}" -n "$NAMESPACE" get pods -l app=dagster -o wide

# --- verification from INSIDE the pod ---
# Read the rendered instance file, not the pod log: on an unchanged pod the startup
# [render-instance] lines have rotated out of `kubectl logs`.
echo ">> Rendered dagster.yaml (secrets are env refs):"
CONF="$("${KUBECTL[@]}" -n "$NAMESPACE" exec deploy/dagster -- sh -c 'cat "${DAGSTER_HOME:-/dagster_home}/dagster.yaml"' 2>&1)" \
  || { printf '%s\n' "$CONF" | sed 's/^/   /'; echo "   ERROR: could not read dagster.yaml from the pod."; exit 1; }
printf '%s\n' "$CONF" | sed 's/^/   /'
grep -q 'postgres_db:' <<<"$CONF" || { echo "   ERROR: Dagster is not on shared PostgreSQL storage."; exit 1; }
[[ "$REQUIRE_S3" == "true" ]] && { grep -q 'S3ComputeLogManager' <<<"$CONF" || { echo "   ERROR: compute logs are not on S3."; exit 1; }; }

echo ">> GraphQL + code locations (from the pod):"
"${KUBECTL[@]}" -n "$NAMESPACE" exec -i deploy/dagster -- python - <<PY
import json, sys, urllib.request
q = {"query": "{ version workspaceOrError { __typename ... on Workspace { locationEntries { name loadStatus locationOrLoadError { __typename } } } } }"}
r = urllib.request.Request("http://localhost:$DAGSTER_PORT/graphql", json.dumps(q).encode(), {"Content-Type": "application/json"})
d = json.load(urllib.request.urlopen(r, timeout=20))["data"]
print("   dagster version:", d["version"])
bad = []
for e in d["workspaceOrError"].get("locationEntries", []):
    ok = e["locationOrLoadError"]["__typename"] == "RepositoryLocation" and e["loadStatus"] == "LOADED"
    print("   %-28s %s" % (e["name"], "OK" if ok else "FAILED (%s)" % e["locationOrLoadError"]["__typename"]))
    if not ok: bad.append(e["name"])
sys.exit(1 if bad or not d["workspaceOrError"].get("locationEntries") else 0)
PY
echo ">> Done. customer360-backend (Dagster) is running on VKS. The Service is a NodePort (private node IP, no auth on the UI):"
echo "   in-cluster: dagster:$DAGSTER_PORT    node: <node-ip>:30300    local: kubectl -n $NAMESPACE port-forward svc/dagster $DAGSTER_PORT:$DAGSTER_PORT"
echo "   Cutover consumers (API DAGSTER_HOST/PORT, ../load_balancer, ../proxy) are described in README.md \"Cutover\"."

. "$ROOT/lib/record_deploy.sh"
record_deployment "$ENV" "$SERVICE" "$IMAGE" success
