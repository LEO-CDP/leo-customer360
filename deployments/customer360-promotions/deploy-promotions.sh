#!/usr/bin/env bash
# Deploy customer360-promotions (c360 Promotions, FastAPI :9009) onto the GreenNode VKS
# Kubernetes cluster as an autoscaling Deployment.
#   ./deploy-promotions.sh <uat|prod> [deploy|destroy]
#
# Replaces the old vServer model (one docker container on the shared api box for uat, a
# dedicated "ads" box for prod). Kubernetes now owns replication, restarts, rollouts and
# scaling; the manifests live in ./k8s/base and this script renders the env-specific
# overlay from the SAME Terraform state the vServer deploy used:
#   * Postgres (../postgres): the leo_ads schema in the shared customer360 DB. REQUIRED —
#     the app raises on startup when it cannot reach the DB, so a pod started during an
#     outage crash-loops rather than serving errors.
#   * Redis (../cache, on the api box): response cache only. The app fails OPEN when Redis
#     is unreachable, so this is not a hard dependency.
#   * The app Service is type LoadBalancer on :9009, so Caddy's ADS_UPSTREAM keeps its
#     existing "ip:9009" shape (../proxy). After the first deploy, set ads_upstream in
#     proxy/overlays/<env>.tfvars to the Service EXTERNAL-IP and redeploy Caddy.
#
# The leo_ads SCHEMA IS NO LONGER BOOTSTRAPPED HERE. There is no VM to run psql on, and
# ../postgres/run-sql.sh already runs SQL against the private vDB through the bastion in a
# fixed order — it is the `db-schema` step of deploy-all.sh and now carries
# customer360-promotions/sql-scripts/ too. Run that step (or ../postgres/run-sql.sh <env>)
# before the first deploy of this service into a fresh database.
#
# Re-runnable. Requires kubectl + a kubeconfig for the VKS cluster (download it from
# https://vks.console.greennode.ai -> cluster -> Kubeconfig). Overrides:
#   KUBECONFIG            path to the VKS kubeconfig (default ./kubeconfig-vks-<env>.yaml,
#                         then ../server/kubeconfig-vks-<env>.yaml, then ~/.kube/vks-<env>.yaml)
#   KUBE_CONTEXT          context to use inside that kubeconfig (default: its current one)
#   PROMOTIONS_NAMESPACE  (default customer360 — shared with customer360-event-api)
#   REDIS_SERVER_KEY      ../server map key of the box running Redis (default "api")
#   GHCR_PULL_TOKEN       long-lived token (PAT, read:packages) for the cluster's
#                         imagePullSecret. Prefer this over GHCR_TOKEN/GITHUB_TOKEN in CI:
#                         a job token is revoked at job end and later pulls then fail.
#   IMAGE_TAG             GHCR tag to deploy (default: image_tag in overlays/<env>.tfvars)
#
# Pod counts are NOT env vars — they are committed, per-env, in
# ./k8s/overlays/<env>/kustomization.yaml so a scaling change is a reviewable diff.
set -euo pipefail
cd "$(dirname "$0")" # deployments/customer360-promotions
MANIFESTS="k8s"

ENV="${1:-}"
case "$ENV" in
  uat | prod) ;;
  *) echo "Usage: ./deploy-promotions.sh <uat|prod> [deploy|destroy]"; exit 1 ;;
esac
ACTION="${2:-deploy}"
case "$ACTION" in
  deploy | destroy) ;;
  *) echo "Usage: ./deploy-promotions.sh <uat|prod> [deploy|destroy]"; exit 1 ;;
esac

[[ -f .env ]] && { set -a; source ./.env; set +a; }

NAMESPACE="${PROMOTIONS_NAMESPACE:-customer360}"
OVERLAY="$MANIFESTS/overlays/$ENV"
[[ -d "$OVERLAY" ]] || { echo "ERROR: no overlay for '$ENV' at $OVERLAY."; exit 1; }

# --- kubeconfig: the VKS cluster is NOT in the default kubeconfig; require an explicit one
#     so we can never accidentally apply into whatever context happens to be current. ---
command -v kubectl >/dev/null 2>&1 || { echo "ERROR: kubectl not found on PATH."; exit 1; }
if [[ -z "${KUBECONFIG:-}" ]]; then
  for c in "./kubeconfig-vks-$ENV.yaml" "../server/kubeconfig-vks-$ENV.yaml" "$HOME/.kube/vks-$ENV.yaml"; do
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
# Deliberately not `cluster-info`: it lists kube-system services, which the scoped CI
# ServiceAccount cannot do. This asks the one question that matters, and works for both a
# cluster-admin kubeconfig and the least-privilege one from
# ../customer360-event-api/ci-access/make-kubeconfig.sh.
"${KUBECTL[@]}" auth can-i create deployments -n "$NAMESPACE" >/dev/null 2>&1 || {
  echo "ERROR: this kubeconfig cannot create Deployments in namespace '$NAMESPACE'."
  echo "       Check the identity, or re-mint the CI kubeconfig with"
  echo "       ../customer360-event-api/ci-access/make-kubeconfig.sh"
  exit 1
}
echo ">> Cluster: $("${KUBECTL[@]}" config current-context)  (namespace $NAMESPACE)"

# --- teardown. Everything this deploy creates carries the kustomize common label, so one
#     label-scoped delete removes the app, its HPA and the hash-suffixed ConfigMap/Secret,
#     whose names are not knowable up front. The NAMESPACE is deliberately left in place;
#     it is shared with customer360-event-api and the rest of the platform. ---
if [[ "$ACTION" == "destroy" ]]; then
  echo ">> Deleting customer360-promotions from namespace $NAMESPACE ..."
  "${KUBECTL[@]}" -n "$NAMESPACE" delete deployment,service,pdb,hpa,configmap,secret \
    -l app.kubernetes.io/name=customer360-promotions --ignore-not-found
  echo ">> Done. Namespace '$NAMESPACE' kept (shared); the GHCR pull secret is kept too."
  echo "   NOTE: the leo_ads schema is NOT dropped — data outlives the deployment."
  exit 0
fi

. "$(cd "$(dirname "$0")/.." && pwd)/lib/tfvars.sh" # tfval + srv_ip

# --- per-env, non-secret service config (same overlay the vServer deploy used) ---
ovl="overlays/$ENV.tfvars"
[[ -f "$ovl" ]] || { echo "ERROR: overlay $ovl not found."; exit 1; }
PROMOTIONS_PORT="$(tfval promotions_port "$ovl")"; PROMOTIONS_PORT="${PROMOTIONS_PORT:-9009}"
DB_SCHEMA="$(tfval promotions_db_schema "$ovl")"; DB_SCHEMA="${DB_SCHEMA:-leo_ads}"
ENVIRONMENT="$(tfval promotions_environment "$ovl")"; ENVIRONMENT="${ENVIRONMENT:-production}"
ROOT_PATH="$(tfval promotions_root_path "$ovl")" # e.g. /ads when fronted by Caddy under that path (empty = served at root)
# Per-pod SQLAlchemy pool. Each POD opens its own, so the total connection count against
# the shared managed vDB is (pool_size + max_overflow) x replicas — a number that grows
# every time the HPA scales out. Keep it explicit per env rather than inheriting the app
# defaults (10 + 20) and discovering the ceiling during a traffic spike.
DB_POOL_SIZE="$(tfval promotions_db_pool_size "$ovl")"
DB_MAX_OVERFLOW="$(tfval promotions_db_max_overflow "$ovl")"

# --- Postgres from the ../postgres deployment ---
pg="../postgres"
DB_NAME="$(tfval db_name "$pg/overlays/$ENV.tfvars")"
DB_USER="$(tfval db_username "$pg/overlays/$ENV.tfvars")"
DB_PASS="${TF_VAR_db_password:-$(tfval db_password "$pg/terraform.tfvars")}"
DB_HOST="$( (cd "$pg" && terraform workspace select "$ENV" >/dev/null 2>&1 && terraform output -raw db_host 2>/dev/null) || true )"
DB_PORT="$( (cd "$pg" && terraform output -raw db_port 2>/dev/null) || echo 5432 )"
: "${DB_NAME:?missing db_name in $pg/overlays/$ENV.tfvars}"
: "${DB_USER:?missing db_username in $pg/overlays/$ENV.tfvars}"
: "${DB_PASS:?missing db_password (set TF_VAR_db_password or $pg/terraform.tfvars)}"
: "${DB_HOST:?could not read db_host from $pg outputs — deploy the database first}"
echo ">> Postgres: ${DB_NAME}.${DB_SCHEMA}@${DB_HOST}:${DB_PORT} (user $DB_USER)"
echo "   NOTE: the schema itself is bootstrapped by ../postgres/run-sql.sh (deploy-all step 'db-schema')."

# --- Redis — the response cache. Unlike the vServer deploy this can no longer be
#     127.0.0.1: the pods do not run on the api box, so use its PRIVATE VPC address.
#     The app fails open when Redis is unreachable, so this is best-effort. ---
SERVERS_JSON="$( (cd ../server && terraform workspace select "$ENV" >/dev/null 2>&1 && terraform output -json servers 2>/dev/null) || true )"
[[ -n "$SERVERS_JSON" ]] || { echo "ERROR: no ../server servers output for $ENV — deploy the server first."; exit 1; }
cache="../cache"
REDIS_PASS="${TF_VAR_redis_password:-$(tfval redis_password "$cache/terraform.tfvars")}"
if [[ "$ENV" == "uat" ]]; then
  REDIS_HOST="$(srv_ip "${REDIS_SERVER_KEY:-api}" fixed_ip)"
  REDIS_PORT="$(tfval redis_port "$cache/overlays/uat.tfvars")"; REDIS_PORT="${REDIS_PORT:-6580}"
else
  REDIS_HOST="$( (cd "$cache" && terraform workspace select prod >/dev/null 2>&1 && terraform output -raw redis_host 2>/dev/null) || true )"
  REDIS_PORT="$( (cd "$cache" && terraform output -raw redis_port 2>/dev/null) || true )"; REDIS_PORT="${REDIS_PORT:-6379}"
fi
CACHE_ENABLED="false"; [[ -n "${REDIS_HOST:-}" && -n "${REDIS_PASS:-}" ]] && CACHE_ENABLED="true"
echo ">> Redis: ${REDIS_HOST:-<none>}:${REDIS_PORT:-} (response cache, enabled=$CACHE_ENABLED)"

# --- image: always the CI-built GHCR image (there is no VM to build on, so BUILD_LOCAL
#     from the vServer era is gone) ---
. "$(cd "$(dirname "$0")/.." && pwd)/lib/ghcr.sh"
SERVICE="customer360-promotions"
GHCR_USER="${GHCR_USER:-${GITHUB_ACTOR:-token}}"
# The token here becomes a LONG-LIVED imagePullSecret in the cluster, which is a different
# requirement from the vServer path. There, a deploy did `docker login` + `docker pull`
# inside the job, so a short-lived token was fine. In Kubernetes the kubelet re-reads this
# secret every time it pulls the image — on every scale-up, node replacement and pod
# reschedule, hours or days later. A workflow's GITHUB_TOKEN is revoked when the job ends,
# so a secret built from it works for this deploy and then silently breaks the next pull.
# Prefer GHCR_PULL_TOKEN (a PAT with read:packages, or a bot account's token).
GHCR_PULL_TOKEN="${GHCR_PULL_TOKEN:-}"
if [[ -n "$GHCR_PULL_TOKEN" ]]; then
  GHCR_TOKEN="$GHCR_PULL_TOKEN"
else
  GHCR_TOKEN="${GHCR_TOKEN:-${GITHUB_TOKEN:-}}"
  if [[ -n "${GITHUB_ACTIONS:-}" && -n "$GHCR_TOKEN" ]]; then
    echo ">> WARNING: building the imagePullSecret from the job's GITHUB_TOKEN, which is"
    echo "            revoked when this job ends. This deploy will work, but the next pod"
    echo "            pull (scale-up, reschedule, node replacement) will ImagePullBackOff."
    echo "            Set a GHCR_PULL_TOKEN secret (PAT with read:packages) to fix it."
  fi
fi
IMAGE="$(image_ref "$SERVICE" "$(resolve_tag "overlays/$ENV.tfvars")")"
echo ">> Image: $IMAGE"

# --- OpenTelemetry request tracing (OTLP -> the monitoring-box Jaeger). ---
. "$(cd "$(dirname "$0")/.." && pwd)/lib/otel.sh"
JAEGER_HOST="$(srv_ip "${MON_SERVER_KEY:-api}" fixed_ip)"; JAEGER_HOST="${JAEGER_HOST:-127.0.0.1}"
OTEL_ENABLED="${OTEL_ENABLED:-$(tfval otel_enabled "$ovl")}"

# --- render the env overlay (gitignored: it holds the DB and Redis credentials) ---
GEN="$MANIFESTS/generated/$ENV"
rm -rf "$GEN"; mkdir -p "$GEN"
umask 077

# Every key carries the app's pydantic env_prefix (core/config.py: env_prefix
# "c360_PROMOTION_"). A key without it is silently ignored by the settings model.
{
  echo "c360_PROMOTION_ENVIRONMENT=$ENVIRONMENT"
  echo "c360_PROMOTION_DB_HOST=$DB_HOST"
  echo "c360_PROMOTION_DB_PORT=$DB_PORT"
  echo "c360_PROMOTION_DB_NAME=$DB_NAME"
  echo "c360_PROMOTION_DB_USER=$DB_USER"
  echo "c360_PROMOTION_DB_SCHEMA=$DB_SCHEMA"
  [[ -n "$DB_POOL_SIZE" ]] && echo "c360_PROMOTION_DB_POOL_SIZE=$DB_POOL_SIZE"
  [[ -n "$DB_MAX_OVERFLOW" ]] && echo "c360_PROMOTION_DB_MAX_OVERFLOW=$DB_MAX_OVERFLOW"
  echo "c360_PROMOTION_REDIS_HOST=${REDIS_HOST:-}"
  echo "c360_PROMOTION_REDIS_PORT=${REDIS_PORT:-6580}"
  echo "c360_PROMOTION_CACHE_ENABLED=$CACHE_ENABLED"
  # Read via os.getenv in core/application.py, NOT through the settings model — it is the
  # public mount point behind Caddy (/ads), and it must survive the move or Swagger's
  # openapi.json and every redirect lose the prefix.
  echo "c360_PROMOTION_ROOT_PATH=$ROOT_PATH"
  otel_env_lines "$SERVICE" "$ENV" "$JAEGER_HOST"
} > "$GEN/config.env"

{
  echo "c360_PROMOTION_DB_PASSWORD=$DB_PASS"
  [[ -n "${REDIS_PASS:-}" ]] && echo "c360_PROMOTION_REDIS_PASSWORD=$REDIS_PASS"
} > "$GEN/secret.env"

# Pin the image by digest when image_ref resolved one, else by tag.
if [[ "$IMAGE" == *@* ]]; then
  IMG_LINE="    newName: ${IMAGE%@*}
    digest: ${IMAGE#*@}"
else
  IMG_LINE="    newName: ${IMAGE%:*}
    newTag: ${IMAGE##*:}"
fi

cat > "$GEN/kustomization.yaml" <<KUSTOMIZE
# GENERATED by deploy-promotions.sh — do not edit; re-run the script instead.
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: $NAMESPACE

# Pod counts come from the committed overlay; this layer only adds what is resolved at
# deploy time (image digest + Terraform-derived config).
resources:
  - ../../overlays/$ENV

images:
  - name: ghcr.io/leo-cdp/leo-customer360/customer360-promotions
$IMG_LINE

# Hash-suffixed names are deliberate: a config or secret change renames the object,
# which rolls the pods so the new values actually take effect.
configMapGenerator:
  - name: promotions-config
    envs: [config.env]

secretGenerator:
  - name: promotions-secrets
    envs: [secret.env]
KUSTOMIZE

# --- namespace first, then the GHCR pull secret it must live in, then everything else.
#     Both are shared with customer360-event-api and are created idempotently by whichever
#     service deploys first. ---
"${KUBECTL[@]}" create namespace "$NAMESPACE" --dry-run=client -o yaml | "${KUBECTL[@]}" apply -f - >/dev/null
if [[ -n "$GHCR_TOKEN" ]]; then
  "${KUBECTL[@]}" -n "$NAMESPACE" create secret docker-registry ghcr-pull \
    --docker-server=ghcr.io --docker-username="$GHCR_USER" --docker-password="$GHCR_TOKEN" \
    --dry-run=client -o yaml | "${KUBECTL[@]}" apply -f - >/dev/null
  echo ">> GHCR pull secret refreshed."
else
  "${KUBECTL[@]}" -n "$NAMESPACE" get secret ghcr-pull >/dev/null 2>&1 || {
    echo "ERROR: no GHCR_PULL_TOKEN/GHCR_TOKEN and no existing 'ghcr-pull' secret in $NAMESPACE."
    echo "       The image is private; pods would ImagePullBackOff. Export GHCR_PULL_TOKEN and re-run."
    exit 1
  }
  echo ">> GHCR pull secret: reusing the existing one (no token given)."
fi

echo ">> Applying manifests (overlay: $ENV) ..."
"${KUBECTL[@]}" apply -k "$GEN"

echo ">> Waiting for the rollout ..."
# A failure here is very often the database: the app RAISES on startup when Postgres is
# unreachable, so the symptom is CrashLoopBackOff rather than a pod that starts unready.
"${KUBECTL[@]}" -n "$NAMESPACE" rollout status deploy/promotions --timeout=5m || {
  echo "   ROLLOUT FAILED. Most likely the pods cannot reach Postgres at $DB_HOST:$DB_PORT,"
  echo "   or the leo_ads schema was never created (run ../postgres/run-sql.sh $ENV). Logs:"
  "${KUBECTL[@]}" -n "$NAMESPACE" logs -l app=promotions --tail=30 || true
  exit 1
}

echo "   --- pods ---"
"${KUBECTL[@]}" -n "$NAMESPACE" get pods -l app=promotions -o wide
echo "   --- autoscaling ---"
"${KUBECTL[@]}" -n "$NAMESPACE" get hpa promotions

# --- dependency check from INSIDE the cluster. This is the migration's real risk: the
#     pods must reach the private managed Postgres from the node subnet. /health answers
#     503 with database=unreachable when they cannot.
#
#     It also checks the SCHEMA, which /health does not. /health only runs SELECT 1, and
#     /health/database reports the schema name as a hard-coded string — so both pass
#     against a database that has no leo_ads tables at all. That could not happen while
#     this script created the schema itself; now that the bootstrap lives in
#     ../postgres/run-sql.sh, a deploy CAN land on an unprepared database and look green
#     while every /serve request fails. Assert it here instead of finding out in traffic. ---
echo ">> Dependency check (from a running pod):"
# 0 = healthy, 2 = reachable but the schema is empty, anything else = probe failed.
# Initialised to 0: `|| DEP_RC=$?` only fires on failure, so the success path must already
# hold the success value.
DEP_RC=0
"${KUBECTL[@]}" -n "$NAMESPACE" exec deploy/promotions -- python -c "
import sys, urllib.request, urllib.error

try:
    print('   health:  ' + urllib.request.urlopen('http://localhost:$PROMOTIONS_PORT/health', timeout=10).read().decode())
except urllib.error.HTTPError as e:
    print('   health:  HTTP %s: %s' % (e.code, e.read().decode()))
except Exception as e:
    print('   health:  unreachable (%s)' % e); sys.exit(1)

# Reuse the app's own engine and settings — same DSN, same credentials, no new deps.
from sqlalchemy import text
from core.config import db_promotions_engine, promotions_settings
schema = promotions_settings.db_schema
try:
    with db_promotions_engine.connect() as c:
        tables = c.execute(
            text('select count(*) from information_schema.tables where table_schema = :s'),
            {'s': schema},
        ).scalar()
except Exception as e:
    print('   schema:  could not query information_schema (%s)' % e); sys.exit(1)
print('   schema:  %s -> %d tables' % (schema, tables))
sys.exit(0 if tables else 2)
" || DEP_RC=$?

if [[ "$DEP_RC" == "2" ]]; then
  echo "   ERROR: the '$DB_SCHEMA' schema is EMPTY on $DB_HOST. The pods are running and can"
  echo "          reach Postgres, but there are no tables to serve from — every ad request"
  echo "          will fail. The schema bootstrap is a separate step now:"
  echo "            ../postgres/run-sql.sh $ENV"
  exit 1
elif [[ "$DEP_RC" != "0" ]]; then
  echo "   WARNING: dependency check failed — check 'kubectl logs -n $NAMESPACE deploy/promotions'."
fi

# The Service is what Caddy will point at, so confirm it actually has endpoints. A pod can
# be Running while the readiness probe keeps it out of the Service, which looks fine in
# `get pods` and serves nothing through the LoadBalancer.
READY_EPS="$("${KUBECTL[@]}" -n "$NAMESPACE" get endpoints promotions -o jsonpath='{range .subsets[*].addresses[*]}{.ip}{"\n"}{end}' 2>/dev/null | grep -c . || true)"
echo ">> Service endpoints: ${READY_EPS:-0} ready pod(s) behind svc/promotions"
[[ "${READY_EPS:-0}" == "0" ]] && echo "   WARNING: no ready endpoints — the LoadBalancer has nothing to forward to."

LB_IP="$("${KUBECTL[@]}" -n "$NAMESPACE" get svc promotions -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)"
[[ -z "$LB_IP" ]] && LB_IP="$("${KUBECTL[@]}" -n "$NAMESPACE" get svc promotions -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)"
# VKS reports the LoadBalancer as <ip>.nip.io rather than a bare ip. nip.io is a
# third-party wildcard DNS service that resolves <ip>.nip.io -> <ip>, so keeping the
# suffix would put someone else's DNS in front of every proxied request for no gain.
# Strip it and use the address directly — the same thing data_upstream does for event-api.
LB_IP="${LB_IP%.nip.io}"

SCALE_RANGE="$("${KUBECTL[@]}" -n "$NAMESPACE" get hpa promotions -o jsonpath='{.spec.minReplicas}-{.spec.maxReplicas}' 2>/dev/null || true)"
echo ">> Done. customer360-promotions is running on VKS (HPA scaling ${SCALE_RANGE:-?} pods on CPU)."
if [[ -n "$LB_IP" ]]; then
  echo "   Service LoadBalancer: $LB_IP:$PROMOTIONS_PORT"
  echo "   NEXT: set  ads_upstream = \"$LB_IP:$PROMOTIONS_PORT\"  in ../proxy/overlays/$ENV.tfvars, then ../proxy/deploy-caddy.sh $ENV"
else
  echo "   Service LoadBalancer: still pending — re-check with:"
  echo "     kubectl -n $NAMESPACE get svc promotions -w"
  echo "   Then set ads_upstream to <EXTERNAL-IP>:$PROMOTIONS_PORT in ../proxy/overlays/$ENV.tfvars and redeploy Caddy."
fi
echo "   Logs:  kubectl -n $NAMESPACE logs -f deploy/promotions"

# --- release ledger: record this deploy to the GitHub Deployments API (best-effort) ---
. "$(cd "$(dirname "$0")/.." && pwd)/lib/record_deploy.sh"
record_deployment "$ENV" "$SERVICE" "$IMAGE" success
