#!/usr/bin/env bash
# Delete the local kind cluster (destroys all local data).
set -euo pipefail
CLUSTER="${KIND_CLUSTER:-customer360}"
kind delete cluster --name "$CLUSTER"
