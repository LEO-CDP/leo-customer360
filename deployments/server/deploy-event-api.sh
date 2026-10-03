#!/usr/bin/env bash
# Deploy customer360-event-api (FastAPI event ingestion) onto the GreenNode VKS
# Kubernetes cluster as an autoscaling Deployment — 3 pods by default.
#   ./deploy-event-api.sh <uat|prod> [deploy|destroy]
#
# Replaces the old vServer model (N docker replicas behind a local nginx LB on the
# "tracking" box). Kubernetes now owns replication, restarts, rollouts and scaling;
# the manifests live in ../customer360-event-api/base and this script renders the
# env-specific overlay from the SAME Terraform state the vServer deploy used:
#   * S3-compatible object storage (../storage — vStorage on VNG): the durable NDJSON sink.
#   * Redis (../cache, on the api box): REQUIRED by the default Redis Streams tracking
#     queue; the IP rate-limit + session metadata still fail OPEN when Redis is degraded.
#     Reached over the private VPC — the VKS worker nodes MUST be able to route to it.
#   * The app Service is type LoadBalancer on :8010, so Caddy's DATA_UPSTREAM keeps its
#     existing "ip:8010" shape (../proxy). After the first deploy, set data_upstream in
#     proxy/overlays/<env>.tfvars to the Service EXTERNAL-IP and redeploy Caddy.
#
# Re-runnable. Requires kubectl + a kubeconfig for the VKS cluster (download it from
# https://vks.console.greennode.ai -> cluster -> Kubeconfig). Overrides:
#   KUBECONFIG           path to the VKS kubeconfig (default ./kubeconfig-vks-<env>.yaml,
#                        then ~/.kube/vks-<env>.yaml)
#   KUBE_CONTEXT         context to use inside that kubeconfig (default: its current one)
#   EVENT_API_NAMESPACE  (default customer360)
#   REDIS_SERVER_KEY     ../server map key of the box running Redis (default "api")
#   GHCR_PULL_TOKEN      long-lived token (PAT, read:packages) for the cluster's
#                        imagePullSecret. Prefer this over GHCR_TOKEN/GITHUB_TOKEN in CI:
#                        a job token is revoked at job end and later pulls then fail.
#   IMAGE_TAG            GHCR tag to deploy (default: image_tag in overlays/<env>.tfvars)
#   EVENT_API_RATE_LIMIT_RPS | *_REQUESTS / *_WINDOW_SECONDS   per-client-IP rate limit
#
# Pod counts and the requests/sec target are NOT env vars — they are committed, per-env,
# in ../customer360-event-api/overlays/<env>/kustomization.yaml so a scaling change is a
# reviewable diff. Autoscaling is driven by KEDA on real ingest RPS; see that folder's README.
#
# NOT done here (unlike the vServer script): the c360-master-profiles bucket bootstrap.
# customer360-event-api never reads MASTER_PROFILE_S3_BUCKET — deploy-api.sh and
# deploy-backend.sh already ensure that bucket for the services that do.
set -euo pipefail
cd "$(dirname "$0")"                 # deployments/server
MANIFESTS="../customer360-event-api"

ENV="${1:-}"
case "$ENV" in
  uat | prod) ;;
  *) echo "Usage: ./deploy-event-api.sh <uat|prod> [deploy|destroy]"; exit 1 ;;
esac
ACTION="${2:-deploy}"
case "$ACTION" in
  deploy | destroy) ;;
  *) echo "Usage: ./deploy-event-api.sh <uat|prod> [deploy|destroy]"; exit 1 ;;
esac

[[ -f .env ]] && { set -a; source ./.env; set +a; }

NAMESPACE="${EVENT_API_NAMESPACE:-customer360}"
OVERLAY="$MANIFESTS/overlays/$ENV"
[[ -d "$OVERLAY" ]] || { echo "ERROR: no overlay for '$ENV' at $OVERLAY."; exit 1; }

# --- kubeconfig: the VKS cluster is NOT in the default kubeconfig; require an explicit one
#     so we can never accidentally apply into whatever context happens to be current. ---
command -v kubectl >/dev/null 2>&1 || { echo "ERROR: kubectl not found on PATH."; exit 1; }
if [[ -z "${KUBECONFIG:-}" ]]; then
  for c in "./kubeconfig-vks-$ENV.yaml" "$HOME/.kube/vks-$ENV.yaml"; do
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
# cluster-admin kubeconfig and the least-privilege one from ci-access/make-kubeconfig.sh.
"${KUBECTL[@]}" auth can-i create deployments -n "$NAMESPACE" >/dev/null 2>&1 || {
  echo "ERROR: this kubeconfig cannot create Deployments in namespace '$NAMESPACE'."
  echo "       Check the identity, or re-mint the CI kubeconfig with"
  echo "       $MANIFESTS/ci-access/make-kubeconfig.sh"
  exit 1
}
echo ">> Cluster: $("${KUBECTL[@]}" config current-context)  (namespace $NAMESPACE)"

# --- teardown. Everything this deploy creates carries the kustomize common label, so one
#     label-scoped delete removes the app, the scaler and the bundled Prometheus — including
#     the hash-suffixed ConfigMap/Secret, whose names are not knowable up front. The
#     NAMESPACE is deliberately left in place; it is shared with the rest of the platform. ---
if [[ "$ACTION" == "destroy" ]]; then
  KINDS="deployment,service,pdb,configmap,secret,serviceaccount,role,rolebinding"
  # scaledobjects only exist as a type when KEDA is installed; asking for an unknown
  # resource type is a hard error, so only include it when the CRD is there.
  "${KUBECTL[@]}" get crd scaledobjects.keda.sh >/dev/null 2>&1 && KINDS="$KINDS,scaledobject"
  echo ">> Deleting customer360-event-api from namespace $NAMESPACE ..."
  "${KUBECTL[@]}" -n "$NAMESPACE" delete $KINDS     -l app.kubernetes.io/name=customer360-event-api --ignore-not-found
  # The pre-KEDA layout's hand-written HPA, if this env predates the KEDA switch.
  "${KUBECTL[@]}" -n "$NAMESPACE" delete hpa event-api --ignore-not-found >/dev/null 2>&1 || true
  echo ">> Done. Namespace '$NAMESPACE' kept (shared); the GHCR pull secret is kept too."
  exit 0
fi

# --- KEDA drives the autoscaling, so its CRDs must exist before we apply a ScaledObject.
#     Installing an operator is a CLUSTER-WIDE change, so it is opt-in rather than silent. ---
KEDA_VERSION="${KEDA_VERSION:-v2.17.1}"
KEDA_MANIFEST="https://github.com/kedacore/keda/releases/download/${KEDA_VERSION}/keda-${KEDA_VERSION#v}.yaml"
if ! "${KUBECTL[@]}" get crd scaledobjects.keda.sh >/dev/null 2>&1; then
  echo "ERROR: the KEDA operator is not installed in this cluster (no scaledobjects.keda.sh CRD)."
  echo "       customer360-event-api scales on request rate via a KEDA ScaledObject."
  echo "       Install it once, cluster-wide, with an ADMIN kubeconfig (the scoped CI identity"
  echo "       cannot create CRDs), then re-run this script:"
  echo "         kubectl apply --server-side -f $KEDA_MANIFEST"
  exit 1
fi
echo ">> KEDA: present ($("${KUBECTL[@]}" get crd scaledobjects.keda.sh -o jsonpath='{.spec.versions[0].name}'))"

. "$(cd "$(dirname "$0")/.." && pwd)/lib/tfvars.sh"   # tfval + srv_ip

# --- Object storage (S3-compatible) from the ../storage deployment ---
store="../storage"
S3_ENDPOINT="$(tfval s3_endpoint "$store/overlays/$ENV.tfvars")"
S3_REGION="$(tfval region "$store/overlays/$ENV.tfvars")"; S3_REGION="${S3_REGION:-us-east-1}"
S3_ACCESS_KEY="${TF_VAR_access_key:-$(tfval access_key "$store/terraform.tfvars")}"
S3_SECRET_KEY="${TF_VAR_secret_key:-$(tfval secret_key "$store/terraform.tfvars")}"
S3_AUTO_CREATE="${S3_AUTO_CREATE_BUCKETS:-$(tfval s3_auto_create_buckets "$store/overlays/$ENV.tfvars")}"; S3_AUTO_CREATE="${S3_AUTO_CREATE:-true}"
: "${S3_ENDPOINT:?could not read s3_endpoint from $store/overlays/$ENV.tfvars}"
: "${S3_ACCESS_KEY:?missing vStorage access_key (set TF_VAR_access_key or $store/terraform.tfvars)}"
: "${S3_SECRET_KEY:?missing vStorage secret_key (set TF_VAR_secret_key or $store/terraform.tfvars)}"
echo ">> S3: $S3_ENDPOINT (region $S3_REGION, path-style, auto_create=$S3_AUTO_CREATE)"

# --- Redis — reuse the api-box cache Redis over the private VPC. The Redis Streams queue
#     backend REQUIRES it: without Redis the ingest endpoint returns 503. ---
terraform workspace select "$ENV" >/dev/null 2>&1 || { echo "ERROR: no '$ENV' server workspace — deploy the server first."; exit 1; }
SERVERS_JSON="$(terraform output -json servers 2>/dev/null || true)"
[[ -n "$SERVERS_JSON" ]] || { echo "ERROR: no servers output."; exit 1; }
cache="../cache"
REDIS_SERVER_KEY="${REDIS_SERVER_KEY:-api}"
REDIS_PASS="${TF_VAR_redis_password:-$(tfval redis_password "$cache/terraform.tfvars")}"
REDIS_HOST="$(srv_ip "$REDIS_SERVER_KEY" fixed_ip)"
REDIS_PORT="$(tfval redis_port "$cache/overlays/$ENV.tfvars")"; REDIS_PORT="${REDIS_PORT:-6580}"
[[ -n "$REDIS_HOST" ]] || { echo "ERROR: no private IP for server key '$REDIS_SERVER_KEY' — the Redis Streams queue cannot work without it."; exit 1; }
echo ">> Redis: ${REDIS_HOST}:${REDIS_PORT} (tracking stream + rate limit + session cache)"
echo "   NOTE: the VKS worker nodes must route to this private IP. The health check at the"
echo "         end of this script reports whether the pods actually reached it."

# --- optional rate-limit override (app default 120 req / 60s per client IP). ---
RL_REQUESTS="${EVENT_API_RATE_LIMIT_REQUESTS:-}"
RL_WINDOW="${EVENT_API_RATE_LIMIT_WINDOW_SECONDS:-}"
if [[ -n "${EVENT_API_RATE_LIMIT_RPS:-}" ]]; then RL_REQUESTS="$EVENT_API_RATE_LIMIT_RPS"; RL_WINDOW="1"; fi
[[ -n "$RL_REQUESTS" ]] && echo ">> Rate limit: $RL_REQUESTS req / ${RL_WINDOW:-60}s per client IP" || echo ">> Rate limit: app default (120 req / 60s)"

# --- image: always the CI-built GHCR image (there is no VM to build on) ---
. "$(cd "$(dirname "$0")/.." && pwd)/lib/ghcr.sh"
SERVICE="customer360-event-api"
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

# --- OpenTelemetry request tracing (OTLP -> the api-box Jaeger). OFF unless otel_enabled. ---
. "$(cd "$(dirname "$0")/.." && pwd)/lib/otel.sh"
JAEGER_HOST="$(srv_ip "${MON_SERVER_KEY:-api}" fixed_ip)"; JAEGER_HOST="${JAEGER_HOST:-127.0.0.1}"
OTEL_ENABLED="${OTEL_ENABLED:-$(tfval otel_enabled "overlays/$ENV.tfvars")}"

# --- render the env overlay (gitignored: it holds the S3 + Redis credentials) ---
GEN="$MANIFESTS/generated/$ENV"
rm -rf "$GEN"; mkdir -p "$GEN"
umask 077

{
  echo "ENVIRONMENT=production"
  echo "OBJECT_STORAGE_MODE=s3"
  echo "S3_ENDPOINT_URL=$S3_ENDPOINT"
  echo "S3_REGION=$S3_REGION"
  echo "S3_FORCE_PATH_STYLE=true"
  echo "S3_AUTO_CREATE_BUCKETS=$S3_AUTO_CREATE"
  echo "REDIS_HOST=$REDIS_HOST"
  echo "REDIS_PORT=$REDIS_PORT"
  echo "REDIS_DB=0"
  # redis_stream is the app default; pinned here because it is what makes >1 pod
  # correct — the queue lives in Redis, so any pod can flush any batch and a dead
  # pod's in-flight batches are reclaimed after TRACKING_STREAM_CLAIM_IDLE_MS.
  echo "TRACKING_QUEUE_BACKEND=redis_stream"
  # Pinned, not left to the app defaults: the KEDA redis-streams trigger in
  # base/scaledobject.yaml names this same stream + group, and if the two ever
  # disagreed the autoscaler would silently watch an empty queue forever.
  echo "TRACKING_STREAM_NAME=data-tracking:events"
  echo "TRACKING_STREAM_GROUP=s3-writers"
  [[ -n "$RL_REQUESTS" ]] && echo "TRACKING_RATE_LIMIT_REQUESTS=$RL_REQUESTS"
  [[ -n "$RL_REQUESTS" && -n "$RL_WINDOW" ]] && echo "TRACKING_RATE_LIMIT_WINDOW_SECONDS=$RL_WINDOW"
  otel_env_lines "$SERVICE" "$ENV" "$JAEGER_HOST"
} > "$GEN/config.env"

{
  echo "S3_ACCESS_KEY_ID=$S3_ACCESS_KEY"
  echo "S3_SECRET_ACCESS_KEY=$S3_SECRET_KEY"
  [[ -n "${REDIS_PASS:-}" ]] && echo "REDIS_PASSWORD=$REDIS_PASS"
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
# GENERATED by deploy-event-api.sh — do not edit; re-run the script instead.
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: $NAMESPACE

# Pod counts and the RPS threshold come from the committed overlay; this layer only
# adds what is resolved at deploy time (image digest + Terraform-derived config).
resources:
  - ../../overlays/$ENV

images:
  - name: ghcr.io/leo-cdp/leo-customer360/customer360-event-api
$IMG_LINE

# Hash-suffixed names are deliberate: a config or secret change renames the object,
# which rolls the pods so the new values actually take effect.
configMapGenerator:
  - name: event-api-config
    envs: [config.env]

secretGenerator:
  - name: event-api-secrets
    envs: [secret.env]
KUSTOMIZE

# The ScaledObject's Prometheus URL is fully qualified with the namespace, because the KEDA
# operator queries it from its OWN namespace. base/ hard-codes the default; retarget it when
# the caller overrode EVENT_API_NAMESPACE, or the scaler fails with a DNS lookup error.
if [[ "$NAMESPACE" != "customer360" ]]; then
  cat >> "$GEN/kustomization.yaml" <<PATCH

patches:
  - target: { kind: ScaledObject, name: event-api }
    patch: |-
      - op: replace
        path: /spec/triggers/0/metadata/serverAddress
        value: http://event-api-prometheus.$NAMESPACE.svc.cluster.local:9090
PATCH
fi

# --- namespace first, then the GHCR pull secret it must live in, then everything else ---
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

# Remove the plain HPA from the pre-KEDA layout. KEDA creates its own
# (keda-hpa-event-api); leaving the old one would give the Deployment two autoscalers
# writing the same replica count. Harmless when it was never there.
if "${KUBECTL[@]}" -n "$NAMESPACE" get hpa event-api >/dev/null 2>&1; then
  echo ">> Removing the legacy 'event-api' HPA (KEDA owns scaling now) ..."
  "${KUBECTL[@]}" -n "$NAMESPACE" delete hpa event-api
fi

echo ">> Applying manifests (overlay: $ENV) ..."
"${KUBECTL[@]}" apply -k "$GEN"

echo ">> Waiting for the rollout ..."
if ! "${KUBECTL[@]}" -n "$NAMESPACE" rollout status deploy/event-api --timeout=5m; then
  echo "   --- rollout failed: pods / events ---"
  "${KUBECTL[@]}" -n "$NAMESPACE" get pods -l app=event-api -o wide || true
  "${KUBECTL[@]}" -n "$NAMESPACE" describe pods -l app=event-api | tail -n 80 || true
  "${KUBECTL[@]}" -n "$NAMESPACE" get events --sort-by=.lastTimestamp | tail -n 25 || true
  exit 1
fi
"${KUBECTL[@]}" -n "$NAMESPACE" rollout status deploy/event-api-prometheus --timeout=3m

echo "   --- pods ---"
"${KUBECTL[@]}" -n "$NAMESPACE" get pods -l app=event-api -o wide
echo "   --- autoscaling (KEDA) ---"
"${KUBECTL[@]}" -n "$NAMESPACE" get scaledobject event-api
"${KUBECTL[@]}" -n "$NAMESPACE" get hpa keda-hpa-event-api 2>/dev/null || true

# --- dependency check from INSIDE the cluster. This is the migration's real risk: the
#     pods must reach the api box's private Redis and the vStorage endpoint. /health
#     reports both (and answers 503 when S3 is unreachable, hence the error handling). ---
echo ">> Dependency check (from a running pod):"
"${KUBECTL[@]}" -n "$NAMESPACE" exec deploy/event-api -- python -c "
import urllib.request, urllib.error
try:
    print('   ' + urllib.request.urlopen('http://localhost:8010/health', timeout=10).read().decode())
except urllib.error.HTTPError as e:
    print('   HTTP %s: %s' % (e.code, e.read().decode()))
" || echo "   WARNING: health probe failed — check 'kubectl logs -n $NAMESPACE deploy/event-api'."

LB_IP="$("${KUBECTL[@]}" -n "$NAMESPACE" get svc event-api -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)"
[[ -z "$LB_IP" ]] && LB_IP="$("${KUBECTL[@]}" -n "$NAMESPACE" get svc event-api -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)"

SCALE_RANGE="$("${KUBECTL[@]}" -n "$NAMESPACE" get scaledobject event-api   -o jsonpath='{.spec.minReplicaCount}-{.spec.maxReplicaCount}' 2>/dev/null || true)"
echo ">> Done. customer360-event-api is running on VKS (KEDA scaling ${SCALE_RANGE:-?} pods on ingest RPS)."
if [[ -n "$LB_IP" ]]; then
  echo "   Service LoadBalancer: $LB_IP:8010"
  echo "   NEXT: set  data_upstream = \"$LB_IP:8010\"  in ../proxy/overlays/$ENV.tfvars, then ../proxy/deploy-caddy.sh $ENV"
else
  echo "   Service LoadBalancer: still pending — re-check with:"
  echo "     kubectl -n $NAMESPACE get svc event-api -w"
  echo "   Then set data_upstream to <EXTERNAL-IP>:8010 in ../proxy/overlays/$ENV.tfvars and redeploy Caddy."
fi
echo "   Logs:  kubectl -n $NAMESPACE logs -f deploy/event-api"

# --- release ledger: record this deploy to the GitHub Deployments API (best-effort) ---
. "$(cd "$(dirname "$0")/.." && pwd)/lib/record_deploy.sh"
record_deployment "$ENV" "$SERVICE" "$IMAGE" success
