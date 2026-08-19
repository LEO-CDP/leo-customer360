#!/usr/bin/env bash
# Tear down the vStorage buckets for an environment overlay (terraform destroy).
# Usage:
#   ./undeploy.sh <uat|prod>            # preview + confirm, then destroy EMPTY buckets
#   ./undeploy.sh <uat|prod> --force    # also empties non-empty buckets (deletes ALL objects)
#   ./undeploy.sh <uat|prod> --yes      # skip the typed confirmation (for CI)
#
# Only removes the buckets Terraform created — it does NOT touch the vStorage
# PROJECT (that is billed and managed in the console). Same workspace/overlay
# model as deploy.sh, so uat and prod stay isolated.
#
# NOTE: S3 refuses to delete a NON-EMPTY bucket. Without --force this destroy
# fails on any bucket that still has objects; --force sets force_destroy=true so
# Terraform empties the bucket (irreversibly) first.
set -euo pipefail

LOCK_TIMEOUT="120s"

cd "$(dirname "$0")"

ENV="${1:-}"
case "$ENV" in
  uat | prod) ;;
  *) echo "Usage: ./undeploy.sh <uat|prod> [--force] [--yes]"; exit 1 ;;
esac
shift

FORCE=0
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --yes | -y) ASSUME_YES=1 ;;
    *) echo "Unknown option: $arg (use --force | --yes)"; exit 1 ;;
  esac
done

VAR_FILE="overlays/${ENV}.tfvars"
if [[ ! -f "$VAR_FILE" ]]; then
  echo "ERROR: overlay $VAR_FILE not found."; exit 1
fi

# Load .env (TF_VAR_* secrets/endpoints) into the environment if present.
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source ./.env
  set +a
fi

if [[ ! -f .env && ! -f terraform.tfvars ]]; then
  echo "ERROR: no credentials found (need .env or terraform.tfvars)."; exit 1
fi

# force_destroy is passed as an extra -var only when --force is given, so a normal
# teardown never silently empties a bucket.
DESTROY_VARS=(-var-file="$VAR_FILE")
if [[ "$FORCE" -eq 1 ]]; then
  DESTROY_VARS+=(-var=force_destroy=true)
fi

terraform init -input=false

# Select the workspace for this environment; if it doesn't exist there's nothing
# to destroy.
if ! terraform workspace select "$ENV" 2>/dev/null; then
  echo "No workspace '$ENV' — nothing deployed to tear down."; exit 0
fi

# Preview exactly what will be destroyed.
echo ">> Planning destroy for '$ENV'${FORCE:+ (force)} ..."
terraform plan -destroy -input=false -lock-timeout="$LOCK_TIMEOUT" "${DESTROY_VARS[@]}"

# Typed confirmation (skipped with --yes). Destroy is irreversible.
if [[ "$ASSUME_YES" -ne 1 ]]; then
  echo
  echo "This will DESTROY the above resources in '$ENV'."
  [[ "$FORCE" -eq 1 ]] && echo "--force is set: non-empty buckets WILL be emptied (all objects deleted)."
  printf "Type the environment name (%s) to confirm: " "$ENV"
  read -r reply
  if [[ "$reply" != "$ENV" ]]; then
    echo "Confirmation did not match — aborted."; exit 1
  fi
fi

terraform destroy -auto-approve -input=false -lock-timeout="$LOCK_TIMEOUT" "${DESTROY_VARS[@]}"
echo "Done. Buckets for '$ENV' destroyed. The vStorage project is untouched."
