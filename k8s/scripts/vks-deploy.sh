#!/usr/bin/env bash
# Deploy / migrate the Customer360 app tier (incl. Dagster) to GreenNode VKS.
# Idempotent. Never touches your current kubectl context: KUBECONFIG is required.
#
#   KUBECONFIG=deployments/kubeconfig-vks-uat.yaml SKIP_BUILD=1 TAG=sha-<40-char-sha> \
#   k8s/scripts/vks-deploy.sh [preflight|build|deploy|verify|all]   # applies Dagster resources ONLY
#
# Env: KUBECONFIG (req) REGISTRY (default ghcr.io/leo-cdp/leo-customer360) TAG (default latest) SKIP_BUILD=1 (use CI image)
#      REGISTRY_USER/REGISTRY_PASSWORD (pull secret; skipped if unset)
#      REGISTRY_SERVER (default = REGISTRY host)  NAMESPACE (default customer360)
#      DRY_RUN=1 (render + server dry-run only)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S="$(cd "$DIR/.." && pwd)"
ROOT="$(cd "$K8S/.." && pwd)"
OVL="$K8S/overlays/vks"
NS="${NAMESPACE:-customer360}"
REGISTRY="${REGISTRY:-ghcr.io/leo-cdp/leo-customer360}"  # CI publishes here; cluster already has the ghcr-pull secret
TAG="${TAG:-latest}"
STEP="${1:-all}"

die() { echo "error: $*" >&2; exit 1; }
k() { kubectl --kubeconfig "$KUBECONFIG" "$@"; }

preflight() {
  command -v kubectl >/dev/null || die "'kubectl' not on PATH"
  [ -n "${KUBECONFIG:-}" ] && [ -f "$KUBECONFIG" ] || die "set KUBECONFIG to the VKS kubeconfig file"
  [ -f "$OVL/secret.env" ] || die "create $OVL/secret.env from secret.env.example"
  if grep -n CHANGE_ME "$OVL/patch-config.yaml" "$OVL/secret.env" 2>/dev/null; then
    die "fill every CHANGE_ME above (managed DB/Redis/Kafka/S3 values)"
  fi
  k version -o json >/dev/null 2>&1 || die "cannot reach the cluster with $KUBECONFIG"
  echo "==> preflight ok: $(k config view --minify -o jsonpath='{.clusters[0].cluster.server}')"
}

build() {
  # Only the Dagster image is built here; api/frontend images come from their own CD.
  command -v docker >/dev/null || die "'docker' not on PATH"
  echo "==> building $REGISTRY/customer360-dagster:$TAG"
  docker build -f "$ROOT/customer360-backend/Dockerfile" -t "$REGISTRY/customer360-dagster:$TAG" "$ROOT"
  docker push "$REGISTRY/customer360-dagster:$TAG"
}

ensure_pull_secret() {
  k -n "$NS" get secret ghcr-pull >/dev/null 2>&1 && { echo "==> ghcr-pull already present"; return; }
  [ -n "${REGISTRY_USER:-}" ] && [ -n "${REGISTRY_PASSWORD:-}" ] || die "no ghcr-pull secret in $NS: set REGISTRY_USER/REGISTRY_PASSWORD"
  # Must be long-lived: the kubelet re-reads it on every later pull (see vks-event-api runbook).
  k -n "$NS" create secret docker-registry ghcr-pull \
    --docker-server="${REGISTRY_SERVER:-${REGISTRY%%/*}}" \
    --docker-username="$REGISTRY_USER" --docker-password="$REGISTRY_PASSWORD" \
    --dry-run=client -o yaml | k apply -f -
}

ensure_dagster_db() {
  # Renderer's CREATE DATABASE usually lacks privilege on managed PG; create it here.
  # Reads DB_* from the live ConfigMap/Secret so it matches what the pod will use.
  local host pw user
  host=$(k -n "$NS" get cm c360-config -o jsonpath='{.data.DB_HOST}')
  user=$(k -n "$NS" get cm c360-config -o jsonpath='{.data.DB_USER}' 2>/dev/null || true); user="${user:-postgres}"
  pw=$(k -n "$NS" get secret c360-secrets -o jsonpath='{.data.DB_PASSWORD}' | base64 -d)
  echo "==> ensuring database 'dagster' on $host"
  k -n "$NS" run dagster-db-init --rm -i --restart=Never --image=postgres:16-alpine \
    --env="PGPASSWORD=$pw" --command -- sh -c \
    "psql -h '$host' -U '$user' -d postgres -tAc \"SELECT 1 FROM pg_database WHERE datname='dagster'\" | grep -q 1 || psql -h '$host' -U '$user' -d postgres -c 'CREATE DATABASE dagster'" \
    || echo "warn: could not ensure 'dagster' DB; pre-create it (terraform) if the pod CrashLoops"
}

render() {
  # Wrap the overlay so the registry/tag are applied without editing committed files.
  local tmp; tmp="$(mktemp -d "$K8S/overlays/.render.XXXXXX")"  # sibling of vks/: kustomize rejects absolute resource paths
  cat > "$tmp/kustomization.yaml" <<EOF
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources: ["../vks"]
images:
  - name: REGISTRY.example/customer360-dagster
    newName: $REGISTRY/customer360-dagster
    newTag: "$TAG"
EOF
  echo "$tmp"
}

deploy() {
  k get ns "$NS" >/dev/null 2>&1 || k create ns "$NS"
  tmp="$(render)"; trap 'rm -rf "$tmp"' EXIT
  if [ "${DRY_RUN:-0}" = 1 ]; then mkdir "$tmp/out"; kubectl kustomize "$tmp" -o "$tmp/out"; k apply --dry-run=server -f "$tmp/out/v1_configmap_c360-config.yaml" -f "$tmp/out/v1_secret_c360-secrets.yaml" -f "$tmp/out/apps_v1_deployment_dagster.yaml" -f "$tmp/out/v1_service_dagster.yaml"; return; fi
  ensure_pull_secret
  # The overlay renders the whole app tier; UAT already runs event-api/promotions here and
  # api/frontend/keycloak are not part of this migration, so apply ONLY Dagster's resources.
  local out="$tmp/out"; mkdir "$out"; kubectl kustomize "$tmp" -o "$out"
  k apply -f "$out/v1_configmap_c360-config.yaml" -f "$out/v1_secret_c360-secrets.yaml" \
          -f "$out/apps_v1_deployment_dagster.yaml" -f "$out/v1_service_dagster.yaml"
  ensure_dagster_db
  k -n "$NS" rollout restart deploy/dagster   # pick up the DB if the first start crash-looped
  k -n "$NS" rollout status deploy/dagster --timeout=300s
}

verify() {
  echo "==> renderer log"
  k -n "$NS" logs deploy/dagster 2>/dev/null | grep render-instance || die "no render-instance output"
  k -n "$NS" logs deploy/dagster | grep -q "storage: PostgreSQL" || die "Dagster not on PostgreSQL"
  k -n "$NS" logs deploy/dagster | grep -q "compute logs: S3" || die "compute logs not on S3"
  echo "==> GraphQL from inside the cluster"
  k -n "$NS" exec deploy/dagster -- python -c \
    "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://localhost:3000/server_info').status==200 else 1)" \
    || die "Dagster webserver not answering"
  k -n "$NS" get pods
  echo "==> OK. UI is ClusterIP only: kubectl --kubeconfig \$KUBECONFIG -n $NS port-forward svc/dagster 3000:3000"
}

case "$STEP" in
  preflight) preflight ;;
  build) preflight; build ;;
  deploy) preflight; deploy ;;
  verify) preflight; verify ;;
  all) preflight; [ "${DRY_RUN:-0}" = 1 ] || [ "${SKIP_BUILD:-0}" = 1 ] || build; deploy; [ "${DRY_RUN:-0}" = 1 ] || verify ;;
  *) die "usage: $0 [preflight|build|deploy|verify|all]" ;;
esac
