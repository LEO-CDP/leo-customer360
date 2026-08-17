#!/usr/bin/env bash
# Deploy the GreenNode/VNG Cloud vLB load balancer (NLB) to an environment overlay.
# Usage:
#   ./deploy.sh <uat|prod> plan      # show what will change (default action)
#   ./deploy.sh <uat|prod> apply     # converge — NO-OP if nothing changed
#   ./deploy.sh <uat|prod> destroy   # tear it down
#
# Overlays: per-env, NON-secret config in overlays/<env>.tfvars (committed).
# Secrets (client_id/secret) come from the git-ignored terraform.tfvars or
# .env (TF_VAR_*), shared across envs for now.
#
# State isolation: each env is a separate Terraform WORKSPACE, so uat and prod
# keep independent state and never touch each other's LB. For runs across
# machines/CI, back this with a shared remote backend + locking.
set -euo pipefail

LOCK_TIMEOUT="120s"

cd "$(dirname "$0")"

ENV="${1:-}"
ACTION="${2:-plan}"

case "$ENV" in
  uat|prod) ;;
  *) echo "Usage: ./deploy.sh <uat|prod> [plan|apply|destroy]"; exit 1 ;;
esac

VAR_FILE="overlays/${ENV}.tfvars"
if [[ ! -f "$VAR_FILE" ]]; then
  echo "ERROR: overlay $VAR_FILE not found."; exit 1
fi

# Load .env (TF_VAR_* secrets/endpoints) into the environment if present.
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  # ./ prefix: a source/. arg with no slash is a PATH lookup under POSIX sh
  # (e.g. `sh deploy.sh`) and would miss a cwd-local .env — the ./ forces cwd.
  source ./.env
  set +a
fi

if [[ ! -f .env && ! -f terraform.tfvars ]]; then
  echo "ERROR: no credentials found. Provide them via one of:"
  echo "  cp .env.example .env                           # then fill it in, or"
  echo "  cp terraform.tfvars.example terraform.tfvars   # then fill it in"
  exit 1
fi

terraform init -input=false

# Select (or create) the workspace for this environment -> isolated state.
terraform workspace select "$ENV" 2>/dev/null || terraform workspace new "$ENV"

case "$ACTION" in
  plan)
    terraform plan -input=false -lock-timeout="$LOCK_TIMEOUT" -var-file="$VAR_FILE"
    ;;
  apply)
    # Plan first with -detailed-exitcode: 0 = no changes, 2 = changes, 1 = error.
    # Only apply when there is a real diff, and apply the SAVED plan so what runs
    # is exactly what was reviewed. Re-running with no drift is a clean no-op.
    set +e
    terraform plan -input=false -lock-timeout="$LOCK_TIMEOUT" -var-file="$VAR_FILE" -detailed-exitcode -out=tfplan
    code=$?
    set -e
    case "$code" in
      0) echo "No changes. $ENV is already up to date." ;;
      2) terraform apply -input=false -lock-timeout="$LOCK_TIMEOUT" tfplan ;;
      *) rm -f tfplan; echo "terraform plan failed (exit $code)"; exit "$code" ;;
    esac
    rm -f tfplan
    ;;
  destroy)
    terraform destroy -input=false -lock-timeout="$LOCK_TIMEOUT" -var-file="$VAR_FILE"
    ;;
  *)
    echo "Unknown action: $ACTION (use plan | apply | destroy)"; exit 1 ;;
esac
