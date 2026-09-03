#!/usr/bin/env bash
# Single-click LOCAL bring-up: create kind cluster -> build+load images ->
# apply overlays/local -> wait -> print URLs.  Run from Git Bash / WSL.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" # k8s/scripts
K8S="$(cd "$DIR/.." && pwd)"                          # k8s
CLUSTER="${KIND_CLUSTER:-customer360}"
KCTX="kind-${CLUSTER}"  # pin every kubectl call to the kind cluster, never the
                        # user's current-context (which may be a remote cluster)

for c in kind kubectl docker; do
  command -v "$c" >/dev/null || { echo "error: '$c' not found on PATH" >&2; exit 1; }
done

if [ ! -f "$K8S/overlays/local/secret.env" ]; then
  echo "error: create k8s/overlays/local/secret.env (copy secret.env.example) first" >&2
  exit 1
fi

if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  echo "==> kind cluster '$CLUSTER' already exists"
  # kind still lists the cluster even when its node container is stopped
  # (e.g. after a host / Docker restart), which makes `kind load` fail with
  # "container ... is not running". Ensure the node is up and Ready first.
  # (docker start is a harmless no-op when the container is already running.)
  NODE="${CLUSTER}-control-plane"
  docker start "$NODE" >/dev/null 2>&1 || true
  echo "==> waiting for node '$NODE' to be Ready"
  kubectl --context "$KCTX" wait --for=condition=Ready \
    "node/${NODE}" --timeout=120s || true
else
  echo "==> creating kind cluster '$CLUSTER'"
  kind create cluster --name "$CLUSTER" --config "$K8S/kind/cluster.yaml"
fi

bash "$DIR/build-load.sh"

echo "==> applying overlays/local"
kubectl --context "$KCTX" apply -k "$K8S/overlays/local"

# Phase 0 moved Dagster storage to Postgres and dropped the dagster-home PVC.
# `apply` doesn't prune removed resources, so delete any leftover from a
# pre-Phase-0 cluster. No-op on fresh clusters.
kubectl --context "$KCTX" -n customer360 delete pvc dagster-home --ignore-not-found

echo "==> waiting for all core workloads to become ready (up to 8 min)"
kubectl --context "$KCTX" -n customer360 wait --for=condition=Available \
  deploy --all --timeout=480s || true
for s in kafka postgres redis; do
  kubectl --context "$KCTX" -n customer360 rollout status "statefulset/$s" --timeout=180s || true
done
kubectl --context "$KCTX" -n customer360 get pods

cat <<'EOF'

==> Customer360 is up on kind. URLs (once pods are Ready):
      API        http://localhost:18008/docs
      Frontend   http://localhost:18890
      Keycloak   http://localhost:18080
      Dagster    http://localhost:13000
      MinIO      http://localhost:19000  (console http://localhost:19001)
    Tail a service:   kubectl -n customer360 logs -f deploy/api
    Tear down:        k8s/scripts/down.sh
EOF
