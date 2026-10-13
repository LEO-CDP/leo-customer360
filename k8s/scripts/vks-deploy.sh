#!/usr/bin/env bash
# Deploy / migrate the Customer360 app tier (incl. Dagster) to GreenNode VKS.
# Idempotent. Never touches your current kubectl context: KUBECONFIG is required.
#
#   KUBECONFIG=~/kubeconfig-vks-uat.yaml REGISTRY=ghcr.io/acme TAG=v1 \
#   REGISTRY_USER=u REGISTRY_PASSWORD=tok  k8s/scripts/vks-deploy.sh [preflight|build|deploy|verify|all]
#
# Env: KUBECONFIG (req) REGISTRY (req) TAG (default latest)
#      REGISTRY_USER/REGISTRY_PASSWORD (pull secret; skipped if unset)
#      REGISTRY_SERVER (default = REGISTRY host)  NAMESPACE (default customer360)
#      DRY_RUN=1 (render + server dry-run only)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S="$(cd "$DIR/.." && pwd)"
ROOT="$(cd "$K8S/.." && pwd)"
OVL="$K8S/overlays/vks"
NS="${NAMESPACE:-customer360}"
TAG="${TAG:-latest}"
STEP="${1:-all}"

die() { echo "error: $*" >&2; exit 1; }
k() { kubectl --kubeconfig "$KUBECONFIG" "$@"; }

preflight() {
  for c in kubectl docker; do command -v "$c" >/dev/null || die "'$c' not on PATH"; done
  [ -n "${KUBECONFIG:-}" ] && [ -f "$KUBECONFIG" ] || die "set KUBECONFIG to the VKS kubeconfig file"
  [ -n "${REGISTRY:-}" ] || die "set REGISTRY (e.g. ghcr.io/acme)"
  [ -f "$OVL/secret.env" ] || die "create $OVL/secret.env from secret.env.example"
  if grep -n CHANGE_ME "$OVL/patch-config.yaml" "$OVL/secret.env" 2>/dev/null; then
    die "fill every CHANGE_ME above (managed DB/Redis/Kafka/S3 values)"
  fi
  k version -o json >/dev/null 2>&1 || die "cannot reach the cluster with $KUBECONFIG"
  echo "==> preflight ok: $(k config view --minify -o jsonpath='{.clusters[0].cluster.server}')"
}

build() {
  # Only the Dagster image is built here; api/frontend images come from their own CD.
  echo "==> building $REGISTRY/customer360-dagster:$TAG"
  docker build -f "$ROOT/customer360-backend/Dockerfile" -t "$REGISTRY/customer360-dagster:$TAG" "$ROOT"
  docker push "$REGISTRY/customer360-dagster:$TAG"
}

ensure_pull_secret() {
  [ -n "${REGISTRY_USER:-}" ] && [ -n "${REGISTRY_PASSWORD:-}" ] || { echo "==> REGISTRY_USER/PASSWORD unset: skipping pull secret"; return; }
  # Must be long-lived: the kubelet re-reads it on every later pull (see vks-event-api runbook).
  k -n "$NS" create secret docker-registry registry-pull \
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
  local tmp; tmp="$(mktemp -d)"
  cat > "$tmp/kustomization.yaml" <<EOF
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources: ["$OVL"]
images:
  - name: REGISTRY.example/customer360-dagster
    newName: $REGISTRY/customer360-dagster
    newTag: "$TAG"
EOF
  echo "$tmp"
}

deploy() {
  k get ns "$NS" >/dev/null 2>&1 || k create ns "$NS"
  local tmp; tmp="$(render)"; trap 'rm -rf "$tmp"' RETURN
  if [ "${DRY_RUN:-0}" = 1 ]; then k apply -k "$tmp" --dry-run=server; return; fi
  ensure_pull_secret
  k apply -k "$tmp"
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
  all) preflight; [ "${DRY_RUN:-0}" = 1 ] || build; deploy; [ "${DRY_RUN:-0}" = 1 ] || verify ;;
  *) die "usage: $0 [preflight|build|deploy|verify|all]" ;;
esac
