#!/usr/bin/env bash
# Build all locally-built Customer360 images and load them into the kind cluster.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)" # k8s/scripts -> repo root
CLUSTER="${KIND_CLUSTER:-customer360}"

build() { # <tag> <context-dir> [dockerfile-relative-to-repo] [build-version]
  local tag="$1" ctx="$2" df="${3:-}" build_version="${4:-}"
  echo "==> build $tag"
  if [ -n "$df" ]; then
    if [ -n "$build_version" ]; then
      docker build --build-arg BUILD_VERSION="$build_version" -t "$tag" -f "$REPO/$df" "$REPO/$ctx"
    else
      docker build -t "$tag" -f "$REPO/$df" "$REPO/$ctx"
    fi
  else
    if [ -n "$build_version" ]; then
      docker build --build-arg BUILD_VERSION="$build_version" -t "$tag" "$REPO/$ctx"
    else
      docker build -t "$tag" "$REPO/$ctx"
    fi
  fi
  echo "==> load $tag into kind/$CLUSTER"
  kind load docker-image "$tag" --name "$CLUSTER"
}

build customer360-postgres:local .                              postgres/Dockerfile
build customer360-redis:local    redis
build customer360-api:local      customer360-api
build customer360-frontend:local frontend-admin "" "$(date -u +%Y-%m-%d-%H-%M)"
build customer360-dagster:local  backend-system

# Preload third-party images (everything referenced by the overlay that we do
# NOT build locally) onto the kind node, so pods don't pull them from the
# internet on first start -- slow/flaky on poor connections (minio can take
# >8 min). Derived from the rendered overlay so it never drifts from manifests.
echo "==> preloading third-party images"
kubectl kustomize "$REPO/k8s/overlays/local" 2>/dev/null \
  | grep -oE 'image:[[:space:]]*[^[:space:]]+' | awk '{print $2}' | sort -u \
  | grep -v ':local$' \
  | while read -r img; do
      echo "==> preload $img"
      docker image inspect "$img" >/dev/null 2>&1 \
        || docker pull "$img" \
        || { echo "   (pull failed; pod will pull $img at runtime)"; continue; }
      kind load docker-image "$img" --name "$CLUSTER"
    done || true

echo "==> all images built and loaded"
