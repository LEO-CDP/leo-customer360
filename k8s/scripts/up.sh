#!/usr/bin/env bash
# Single-click LOCAL bring-up: create kind cluster -> build+load images ->
# apply overlays/local -> wait -> print URLs.  Run from Git Bash / WSL.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" # k8s/scripts
K8S="$(cd "$DIR/.." && pwd)"                          # k8s
CLUSTER="${KIND_CLUSTER:-customer360}"

for c in kind kubectl docker; do
  command -v "$c" >/dev/null || { echo "error: '$c' not found on PATH" >&2; exit 1; }
done

if [ ! -f "$K8S/overlays/local/secret.env" ]; then
  echo "error: create k8s/overlays/local/secret.env (copy secret.env.example) first" >&2
  exit 1
fi

if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  echo "==> kind cluster '$CLUSTER' already exists"
else
  echo "==> creating kind cluster '$CLUSTER'"
  kind create cluster --name "$CLUSTER" --config "$K8S/kind/cluster.yaml"
fi

bash "$DIR/build-load.sh"

echo "==> applying overlays/local"
kubectl apply -k "$K8S/overlays/local"

echo "==> waiting for the API to roll out (up to 5 min; other pods continue in background)"
kubectl -n customer360 rollout status deploy/api --timeout=300s || true
kubectl -n customer360 get pods

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
